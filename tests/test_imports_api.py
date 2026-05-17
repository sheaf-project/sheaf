"""Tests for the unified /v1/imports endpoints.

These cover the enqueue + lifecycle surface only. The per-source
runners (PluralKit, Tupperbox, etc.) get their own tests in
phases 3-6 — at this phase the runner is registered but has no
handler for any source, so jobs sit pending and never run.
"""

from __future__ import annotations

import json
import pathlib
import uuid

import httpx

PK_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "pk_export_sample.json"


def _post_file(
    client: httpx.Client,
    *,
    source: str,
    idem_key: str | None = None,
    payload: bytes | None = None,
    options: dict | None = None,
    filename: str = "import.json",
) -> httpx.Response:
    """Helper for POST /v1/imports/file. Defaults to the PK fixture for
    the payload so callers can override on a per-test basis."""
    # b"" is intentional empty-file test input — only fall back to the
    # fixture when payload was not explicitly passed.
    actual = payload if payload is not None else PK_FIXTURE.read_bytes()
    files = {"file": (filename, actual, "application/json")}
    form: dict[str, str] = {
        "source": source,
        "idempotency_key": idem_key or str(uuid.uuid4()),
    }
    if options is not None:
        form["options"] = json.dumps(options)
    return client.post("/v1/imports/file", files=files, data=form)


# --- POST /v1/imports/file -------------------------------------------------


def test_file_create_returns_202_and_pending_job(auth_client: httpx.Client):
    resp = _post_file(auth_client, source="pluralkit_file")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["source"] == "pluralkit_file"
    assert body["counts"] == {}
    assert body["events"] == []
    assert uuid.UUID(body["id"])  # is a UUID


def test_file_create_idempotent_returns_same_job(auth_client: httpx.Client):
    """Same idempotency_key, same user, two POSTs -> same job id, not
    a duplicate. This is the double-click-the-upload-button defence."""
    key = str(uuid.uuid4())
    first = _post_file(auth_client, source="pluralkit_file", idem_key=key)
    assert first.status_code == 202, first.text
    first_id = first.json()["id"]

    second = _post_file(auth_client, source="pluralkit_file", idem_key=key)
    assert second.status_code == 202, second.text
    assert second.json()["id"] == first_id


def test_file_create_rejects_unknown_source(auth_client: httpx.Client):
    resp = _post_file(auth_client, source="bogus_source")
    assert resp.status_code == 422, resp.text


def test_file_create_rejects_legacy_source(auth_client: httpx.Client):
    """The legacy single-source endpoints kept their /v1/import/* prefix.
    The new unified router refuses the deprecated 'pluralkit_api' value
    here — that one's only valid via /v1/imports/api with a token, not
    a file upload."""
    resp = _post_file(auth_client, source="pluralkit_api")
    assert resp.status_code == 422, resp.text


def test_file_create_rejects_empty_file(auth_client: httpx.Client):
    resp = _post_file(auth_client, source="pluralkit_file", payload=b"")
    assert resp.status_code == 400, resp.text


def test_file_create_rejects_bad_options_json(auth_client: httpx.Client):
    """`options` ships as a form field with JSON inside — bad JSON is a
    400, not a 422, because the schema-level validation never gets
    reached. Same shape as the legacy importers' early bail."""
    files = {"file": ("x.json", PK_FIXTURE.read_bytes(), "application/json")}
    resp = auth_client.post(
        "/v1/imports/file",
        files=files,
        data={
            "source": "pluralkit_file",
            "idempotency_key": str(uuid.uuid4()),
            "options": "this is not json",
        },
    )
    assert resp.status_code == 400, resp.text
    assert "not valid JSON" in resp.json()["detail"]


def test_file_create_accepts_options_json(auth_client: httpx.Client):
    resp = _post_file(
        auth_client,
        source="pluralkit_file",
        options={"system_profile": True, "front_history": False},
    )
    assert resp.status_code == 202, resp.text


# --- POST /v1/imports/api --------------------------------------------------


def test_api_create_returns_202_and_pending_job(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/imports/api",
        json={
            "source": "pluralkit_api",
            "idempotency_key": str(uuid.uuid4()),
            "pk_token": "fake-pk-token-not-real",
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["source"] == "pluralkit_api"


def test_api_create_idempotent_returns_same_job(auth_client: httpx.Client):
    key = str(uuid.uuid4())
    first = auth_client.post(
        "/v1/imports/api",
        json={
            "source": "pluralkit_api",
            "idempotency_key": key,
            "pk_token": "tok",
        },
    )
    assert first.status_code == 202, first.text
    second = auth_client.post(
        "/v1/imports/api",
        json={
            "source": "pluralkit_api",
            "idempotency_key": key,
            "pk_token": "tok-different",  # ignored on idempotent match
        },
    )
    assert second.status_code == 202, second.text
    assert second.json()["id"] == first.json()["id"]


def test_api_create_rejects_extra_fields(auth_client: httpx.Client):
    """ImportApiCreateRequest is extra='forbid' — stowaway fields get
    a 422, not a silent drop."""
    resp = auth_client.post(
        "/v1/imports/api",
        json={
            "source": "pluralkit_api",
            "idempotency_key": str(uuid.uuid4()),
            "pk_token": "tok",
            "bogus_extra_field": True,
        },
    )
    assert resp.status_code == 422, resp.text


# --- GET /v1/imports -------------------------------------------------------


def test_list_returns_user_jobs_most_recent_first(auth_client: httpx.Client):
    first = _post_file(auth_client, source="pluralkit_file")
    second = _post_file(auth_client, source="tupperbox_file")
    assert first.status_code == 202 and second.status_code == 202

    resp = auth_client.get("/v1/imports", params={"limit": 50})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    ids = [item["id"] for item in items]
    # Newest first; both should appear.
    assert second.json()["id"] in ids
    assert first.json()["id"] in ids
    assert ids.index(second.json()["id"]) < ids.index(first.json()["id"])


def test_list_paginates_via_next_cursor(auth_client: httpx.Client):
    """limit=1 surfaces next_cursor; passing it back as ?cursor= fetches
    the next page, and the two pages partition the jobs without overlap."""
    first = _post_file(auth_client, source="pluralkit_file")
    second = _post_file(auth_client, source="tupperbox_file")

    page1 = auth_client.get("/v1/imports", params={"limit": 1})
    assert page1.status_code == 200, page1.text
    p1 = page1.json()
    assert len(p1["items"]) == 1
    assert p1["next_cursor"] is not None
    # Newest first — page 1 is the second-created job.
    assert p1["items"][0]["id"] == second.json()["id"]

    # Page 2 via the cursor — the older job, no overlap.
    page2 = auth_client.get(
        "/v1/imports", params={"limit": 1, "cursor": p1["next_cursor"]}
    )
    assert page2.status_code == 200, page2.text
    p2 = page2.json()
    assert len(p2["items"]) == 1
    assert p2["items"][0]["id"] == first.json()["id"]
    assert p2["items"][0]["id"] != p1["items"][0]["id"]


def test_list_rejects_bad_cursor(auth_client: httpx.Client):
    resp = auth_client.get("/v1/imports", params={"cursor": "not-a-timestamp"})
    assert resp.status_code == 422, resp.text


def test_list_excludes_archived_by_default(auth_client: httpx.Client):
    created = _post_file(auth_client, source="pluralkit_file")
    job_id = created.json()["id"]
    # Cancel pending -> moves to cancelled (terminal). Then DELETE again
    # to archive. The archived row should drop out of the default list.
    cancel = auth_client.delete(f"/v1/imports/{job_id}")
    assert cancel.status_code == 204
    archive = auth_client.delete(f"/v1/imports/{job_id}")
    assert archive.status_code == 204

    default = auth_client.get("/v1/imports", params={"limit": 50}).json()
    assert all(item["id"] != job_id for item in default["items"])

    with_archived = auth_client.get(
        "/v1/imports", params={"limit": 50, "include_archived": True}
    ).json()
    assert any(item["id"] == job_id for item in with_archived["items"])


# --- GET /v1/imports/{id} --------------------------------------------------


def test_get_returns_full_detail(auth_client: httpx.Client):
    created = _post_file(auth_client, source="pluralkit_file")
    job_id = created.json()["id"]
    resp = auth_client.get(f"/v1/imports/{job_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == job_id
    assert body["events"] == []


def test_get_404_for_other_user(auth_client: httpx.Client, client: httpx.Client):
    """A job belonging to another account 404s — no leak of cross-user
    id existence."""
    import os

    other_email = f"imports-other-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as other:
        reg = other.post(
            "/v1/auth/register",
            json={"email": other_email, "password": "testpassword123"},
        )
        assert reg.status_code == 201, reg.text
        other.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        other_job = _post_file(other, source="pluralkit_file")
        assert other_job.status_code == 202, other_job.text
        other_id = other_job.json()["id"]

    resp = auth_client.get(f"/v1/imports/{other_id}")
    assert resp.status_code == 404


def test_get_404_for_random_uuid(auth_client: httpx.Client):
    resp = auth_client.get(f"/v1/imports/{uuid.uuid4()}")
    assert resp.status_code == 404


# --- DELETE /v1/imports/{id} -----------------------------------------------


def test_delete_pending_cancels(auth_client: httpx.Client):
    created = _post_file(auth_client, source="pluralkit_file")
    job_id = created.json()["id"]
    resp = auth_client.delete(f"/v1/imports/{job_id}")
    assert resp.status_code == 204
    fresh = auth_client.get(f"/v1/imports/{job_id}").json()
    assert fresh["status"] == "cancelled"
    assert fresh["finished_at"] is not None


def test_delete_terminal_archives(auth_client: httpx.Client):
    created = _post_file(auth_client, source="pluralkit_file")
    job_id = created.json()["id"]
    auth_client.delete(f"/v1/imports/{job_id}")  # cancel -> terminal
    archive = auth_client.delete(f"/v1/imports/{job_id}")
    assert archive.status_code == 204
    # Default list excludes archived; include_archived surfaces it.
    listing = auth_client.get(
        "/v1/imports", params={"include_archived": True, "limit": 50}
    ).json()
    matched = [item for item in listing["items"] if item["id"] == job_id]
    assert matched and matched[0]["archived_at"] is not None


def test_delete_other_user_404(auth_client: httpx.Client):
    """Same 404 leak protection as GET — can't even discover ids."""
    import os

    other_email = f"imports-delother-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as other:
        reg = other.post(
            "/v1/auth/register",
            json={"email": other_email, "password": "testpassword123"},
        )
        other.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        other_id = _post_file(other, source="pluralkit_file").json()["id"]

    resp = auth_client.delete(f"/v1/imports/{other_id}")
    assert resp.status_code == 404


# --- Authn / scope ---------------------------------------------------------


def test_anon_create_rejected(client: httpx.Client):
    resp = _post_file(client, source="pluralkit_file")
    assert resp.status_code in (401, 403)


def test_anon_list_rejected(client: httpx.Client):
    resp = client.get("/v1/imports")
    assert resp.status_code in (401, 403)
