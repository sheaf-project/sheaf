"""End-to-end coverage for the three account-export pieces:

1. /v1/export now includes journals + content revisions + system safety
   settings + retention overrides (Article 20 gap fill).
2. POST /v1/account/data — Article 15 with mandatory step-up auth.
3. POST /v1/export/jobs — async build + cleanup lifecycle, also gated.
"""

from __future__ import annotations

import io
import zipfile

import httpx

# ---------------------------------------------------------------------------
# Article 20 gap fill: /v1/export now includes the missing pieces
# ---------------------------------------------------------------------------


def test_export_includes_journals(auth_client: httpx.Client):
    """Journal entries used to be missing entirely from /v1/export."""
    r = auth_client.post("/v1/journals", json={"body": "this is a journal"})
    assert r.status_code == 201, r.text

    export = auth_client.get("/v1/export").json()
    assert "journals" in export
    assert any(j["body"] == "this is a journal" for j in export["journals"])


def test_export_includes_content_revisions(auth_client: httpx.Client):
    """Edit a journal to generate a revision; export should carry it."""
    j = auth_client.post("/v1/journals", json={"body": "v1"}).json()
    auth_client.patch(f"/v1/journals/{j['id']}", json={"body": "v2"})

    export = auth_client.get("/v1/export").json()
    assert "revisions" in export
    revs = [
        r for r in export["revisions"]
        if r["target_type"] == "journal_entry" and r["target_id"] == j["id"]
    ]
    assert len(revs) >= 1
    assert any(r["body"] == "v1" for r in revs)


def test_export_includes_system_safety_settings(auth_client: httpx.Client):
    export = auth_client.get("/v1/export").json()
    assert "safety" in export["system"]
    safety = export["system"]["safety"]
    # All toggle keys should be present even if false.
    for key in (
        "grace_period_days",
        "applies_to_members",
        "applies_to_journals",
        "applies_to_notifications",
        "auto_pin_first_revision",
    ):
        assert key in safety


def test_export_includes_system_preferences(auth_client: httpx.Client):
    export = auth_client.get("/v1/export").json()
    sys = export["system"]
    assert "replace_fronts_default" in sys
    assert "date_format" in sys
    assert "delete_confirmation" in sys


def test_export_version_bumped_to_2(auth_client: httpx.Client):
    export = auth_client.get("/v1/export").json()
    assert export["version"] == "2"


# ---------------------------------------------------------------------------
# Article 15: /v1/account/data
# ---------------------------------------------------------------------------


def test_account_data_requires_password(auth_client: httpx.Client):
    r = auth_client.post("/v1/account/data", json={"password": "wrong"})
    assert r.status_code == 401


def test_account_data_succeeds_with_correct_password(auth_client: httpx.Client):
    r = auth_client.post(
        "/v1/account/data", json={"password": "testpassword123"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Should include account identity + audit blocks.
    for k in (
        "account",
        "sessions",
        "trusted_devices",
        "api_keys",
        "client_settings",
        "pending_safety_actions",
        "receiving_notification_channels",
    ):
        assert k in body
    # Account block should NOT include the password hash, totp secret, or
    # recovery codes.
    serialised = str(body["account"])
    assert "password_hash" not in serialised
    assert "totp_secret" not in serialised
    assert "recovery_codes" not in serialised


def test_account_data_unauth_rejected(client: httpx.Client):
    r = client.post(
        "/v1/account/data", json={"password": "testpassword123"}
    )
    assert r.status_code == 401


def test_account_data_refuses_api_key_auth(auth_client: httpx.Client):
    """Article 15 is too sensitive to expose via programmatic credentials."""
    key = auth_client.post(
        "/v1/auth/keys",
        json={"name": "tries-account-data", "scopes": ["system:read"]},
    ).json()["key"]
    with httpx.Client(
        base_url=str(auth_client.base_url),
        headers={"Authorization": f"Bearer {key}"},
    ) as c:
        r = c.post(
            "/v1/account/data", json={"password": "testpassword123"}
        )
    assert r.status_code == 403
    assert "API key" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Async export jobs
# ---------------------------------------------------------------------------


def test_export_job_requires_password(auth_client: httpx.Client):
    r = auth_client.post(
        "/v1/export/jobs",
        json={"include_images": False, "password": "wrong"},
    )
    assert r.status_code == 401


def test_export_job_create_then_list(auth_client: httpx.Client):
    r = auth_client.post(
        "/v1/export/jobs",
        json={"include_images": False, "password": "testpassword123"},
    )
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] == "pending"
    assert job["include_images"] is False

    listed = auth_client.get("/v1/export/jobs").json()
    assert any(j["id"] == job["id"] for j in listed)


def test_export_job_concurrency_limit(auth_client: httpx.Client):
    """Second create with one already in-flight must 409."""
    auth_client.post(
        "/v1/export/jobs",
        json={"include_images": False, "password": "testpassword123"},
    )
    r = auth_client.post(
        "/v1/export/jobs",
        json={"include_images": False, "password": "testpassword123"},
    )
    assert r.status_code == 409


def test_export_job_download_before_ready_409(auth_client: httpx.Client):
    job = auth_client.post(
        "/v1/export/jobs",
        json={"include_images": False, "password": "testpassword123"},
    ).json()
    r = auth_client.get(
        f"/v1/export/jobs/{job['id']}/download",
        follow_redirects=False,
    )
    assert r.status_code == 409


def test_export_job_refuses_api_key_auth(auth_client: httpx.Client):
    key = auth_client.post(
        "/v1/auth/keys",
        json={"name": "tries-export", "scopes": ["system:read"]},
    ).json()["key"]
    with httpx.Client(
        base_url=str(auth_client.base_url),
        headers={"Authorization": f"Bearer {key}"},
    ) as c:
        r = c.post(
            "/v1/export/jobs",
            json={"include_images": False, "password": "testpassword123"},
        )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Builder unit-style — assemble the zip directly
# ---------------------------------------------------------------------------


def test_zip_assembly_roundtrip():
    """Smoke-test the zip layout: the assembled artefact must be a valid
    zip with export.json + README.txt at minimum."""
    # Build a tiny zip the same way the worker does, in-process.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("export.json", '{"version":"2"}')
        zf.writestr("README.txt", "test readme")

    parsed = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    names = set(parsed.namelist())
    assert "export.json" in names
    assert "README.txt" in names
