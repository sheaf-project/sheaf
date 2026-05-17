"""Integration tests for the SP-gap bundle:

- Member.is_custom_front flag (custom fronts as non-counting fronting entities)
- Member.emoji (compact visual identifier)
- Front.custom_status (per-fronting-period free-text annotation)

Plus regression coverage for the SP-importer's switch from the description-
prefix hack to the new is_custom_front flag.
"""

import json
import pathlib

import httpx


def test_member_emoji_roundtrips(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/members",
        json={"name": "Foxy", "emoji": "🦊", "color": "#ff6600"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["emoji"] == "🦊"

    # Update path
    resp = auth_client.patch(
        f"/v1/members/{data['id']}",
        json={"emoji": "🌙"},
    )
    assert resp.status_code == 200
    assert resp.json()["emoji"] == "🌙"


def test_member_emoji_clear_via_empty_string(auth_client: httpx.Client):
    """Members can drop their emoji by patching it to null."""
    created = auth_client.post(
        "/v1/members", json={"name": "Wolf", "emoji": "🐺"}
    ).json()
    resp = auth_client.patch(
        f"/v1/members/{created['id']}", json={"emoji": None}
    )
    assert resp.status_code == 200
    assert resp.json()["emoji"] is None


def test_member_is_custom_front_default_false(auth_client: httpx.Client):
    resp = auth_client.post("/v1/members", json={"name": "Real Member"})
    assert resp.status_code == 201
    assert resp.json()["is_custom_front"] is False


def test_member_can_be_marked_custom_front(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/members",
        json={"name": "Asleep", "is_custom_front": True},
    )
    assert resp.status_code == 201
    assert resp.json()["is_custom_front"] is True

    # And toggled off
    resp = auth_client.patch(
        f"/v1/members/{resp.json()['id']}",
        json={"is_custom_front": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_custom_front"] is False


def test_custom_fronts_listed_alongside_members(auth_client: httpx.Client):
    """The /v1/members endpoint returns both kinds; the UI partitions on the
    is_custom_front flag rather than the API doing it."""
    auth_client.post("/v1/members", json={"name": "Real"})
    auth_client.post("/v1/members", json={"name": "Away", "is_custom_front": True})

    members = auth_client.get("/v1/members").json()
    by_name = {m["name"]: m for m in members}
    assert by_name["Real"]["is_custom_front"] is False
    assert by_name["Away"]["is_custom_front"] is True


def test_front_custom_status_encrypts_at_rest_and_decrypts_for_read(
    auth_client: httpx.Client,
):
    """The Front model encrypts custom_status at rest, mirroring the
    encryption pattern used for member descriptions and journal bodies."""
    member = auth_client.post("/v1/members", json={"name": "Alex"}).json()

    resp = auth_client.post(
        "/v1/fronts",
        json={
            "member_ids": [member["id"]],
            "custom_status": "during a tense meeting",
        },
    )
    assert resp.status_code == 201, resp.text
    front = resp.json()
    assert front["custom_status"] == "during a tense meeting"

    # And on the read endpoints
    listed = auth_client.get("/v1/fronts").json()
    assert listed[0]["custom_status"] == "during a tense meeting"

    current = auth_client.get("/v1/fronts/current").json()
    assert current[0]["custom_status"] == "during a tense meeting"


def test_front_custom_status_patch_clear_vs_omit(auth_client: httpx.Client):
    """custom_status uses Pydantic's model_fields_set so a missing field
    means 'keep' while explicit null means 'clear'."""
    member = auth_client.post("/v1/members", json={"name": "Bea"}).json()
    front = auth_client.post(
        "/v1/fronts",
        json={"member_ids": [member["id"]], "custom_status": "first try"},
    ).json()

    # Omit custom_status: keeps the existing value
    resp = auth_client.patch(f"/v1/fronts/{front['id']}", json={})
    assert resp.status_code == 200
    assert resp.json()["custom_status"] == "first try"

    # Explicit null: clears it
    resp = auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"custom_status": None}
    )
    assert resp.status_code == 200
    assert resp.json()["custom_status"] is None

    # New value: replaces
    resp = auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"custom_status": "updated"}
    )
    assert resp.status_code == 200
    assert resp.json()["custom_status"] == "updated"


def test_front_custom_status_optional(auth_client: httpx.Client):
    """No custom_status supplied at create time leaves the field null."""
    member = auth_client.post("/v1/members", json={"name": "Cleo"}).json()
    resp = auth_client.post("/v1/fronts", json={"member_ids": [member["id"]]})
    assert resp.status_code == 201
    assert resp.json()["custom_status"] is None


# --- SP-import path: frontStatuses -> is_custom_front=True ------------------


def _run_file_import(client: httpx.Client, *, source: str, payload: bytes) -> dict:
    """Enqueue a file import through the async runner, drive the runner
    inside the test container, and return the terminal job. Imports moved
    off synchronous endpoints onto the job runner — these SP-gap
    regression tests follow the same enqueue + drive + poll shape as the
    dedicated import-runner tests."""
    import uuid

    from tests._import_runner_helpers import drive_import_runner, wait_for_terminal

    resp = client.post(
        "/v1/imports/file",
        files={"file": ("import.json", payload, "application/json")},
        data={"source": source, "idempotency_key": str(uuid.uuid4())},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["id"]

    drive_import_runner()
    return wait_for_terminal(client, job_id)


def _sp_export_with_custom_fronts() -> bytes:
    return json.dumps(
        {
            "users": [{"username": "Test"}],
            "members": [
                {"_id": "m1", "name": "Real", "private": False},
            ],
            "frontStatuses": [
                {"_id": "cf1", "name": "Asleep", "color": "#888888"},
                {"_id": "cf2", "name": "Away", "color": "#aaaaaa"},
            ],
        }
    ).encode()


def test_sp_import_marks_frontstatuses_as_custom_fronts(
    auth_client: httpx.Client,
):
    payload = _sp_export_with_custom_fronts()
    job = _run_file_import(auth_client, source="simplyplural_file", payload=payload)
    assert job["status"] == "complete", job
    assert job["counts"]["members_imported"] == 1
    assert job["counts"]["custom_fronts_imported"] == 2

    members = auth_client.get("/v1/members").json()
    by_name = {m["name"]: m for m in members}
    assert by_name["Real"]["is_custom_front"] is False
    assert by_name["Asleep"]["is_custom_front"] is True
    assert by_name["Away"]["is_custom_front"] is True


def test_sp_import_no_longer_prefixes_custom_front_descriptions(
    auth_client: httpx.Client,
):
    """The old behaviour prepended '[Imported SP custom front]' to the
    description because there was no flag. With the flag, the description
    should be left alone (or stay null when SP didn't provide one)."""
    payload = json.dumps(
        {
            "users": [{"username": "Test"}],
            "members": [],
            "frontStatuses": [
                {"_id": "cf1", "name": "Asleep", "desc": "snooze", "private": False},
                {"_id": "cf2", "name": "Empty", "private": False},  # no desc
            ],
        }
    ).encode()
    job = _run_file_import(auth_client, source="simplyplural_file", payload=payload)
    assert job["status"] == "complete", job

    members = auth_client.get("/v1/members").json()
    by_name = {m["name"]: m for m in members}
    assert by_name["Asleep"]["description"] == "snooze"
    assert by_name["Empty"]["description"] is None


# --- Roundtrip via Sheaf export/import --------------------------------------


def test_sheaf_export_includes_new_fields(auth_client: httpx.Client):
    auth_client.post(
        "/v1/members",
        json={
            "name": "Foxy",
            "emoji": "🦊",
            "is_custom_front": False,
        },
    )
    auth_client.post(
        "/v1/members",
        json={"name": "Asleep", "is_custom_front": True},
    )
    member_real = next(
        m for m in auth_client.get("/v1/members").json() if m["name"] == "Foxy"
    )
    auth_client.post(
        "/v1/fronts",
        json={
            "member_ids": [member_real["id"]],
            "custom_status": "writing tests",
        },
    )

    export = auth_client.get("/v1/export").json()
    by_name = {m["name"]: m for m in export["members"]}
    assert by_name["Foxy"]["emoji"] == "🦊"
    assert by_name["Foxy"]["is_custom_front"] is False
    assert by_name["Asleep"]["is_custom_front"] is True

    assert export["fronts"][0]["custom_status"] == "writing tests"


def test_pluralkit_id_doesnt_collide_with_emoji(auth_client: httpx.Client):
    """Both fields are short strings; make sure the schemas don't conflate
    them and round-trip cleanly together."""
    resp = auth_client.post(
        "/v1/members",
        json={"name": "Mira", "pluralkit_id": "wyyetr", "emoji": "🦊"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["pluralkit_id"] == "wyyetr"
    assert data["emoji"] == "🦊"


# --- Round-trip via the Sheaf importer, not just the export ----------------


def test_pk_import_does_not_set_custom_front(auth_client: httpx.Client):
    """PK doesn't have a custom-front concept; imported members should all
    land with is_custom_front=False."""
    fixture = (
        pathlib.Path(__file__).parent / "fixtures" / "pk_export_sample.json"
    )
    job = _run_file_import(
        auth_client, source="pluralkit_file", payload=fixture.read_bytes()
    )
    assert job["status"] == "complete", job
    members = auth_client.get("/v1/members").json()
    assert all(m["is_custom_front"] is False for m in members)
