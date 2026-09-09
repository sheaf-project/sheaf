"""End-to-end tests for the Masquerade import runner handler.

Masquerade is a members-only proxy-bot format (like Tupperbox), so the
surface under test is small: the runner plumbing, the preview endpoint,
and the mapping decisions worth locking in:
- name / display_name / avatar_url / color land on the imported member.
- Proxy tags and the `hidden` flag are dropped without error (imported
  members are private by default, so `hidden` adds nothing).
- A payload without a `profiles` array is a wrong-format upload and
  fails cleanly, as does a non-object top level.
"""

from __future__ import annotations

import json
import uuid

import httpx

from tests._import_runner_helpers import (
    drive_import_runner,
    set_member_limit,
    wait_for_terminal,
)


def _masquerade_payload(profiles: list[dict]) -> bytes:
    return json.dumps({"profiles": profiles}).encode()


def _sample_profiles() -> list[dict]:
    return [
        {
            "name": "Alpha",
            "display_name": "The Alpha",
            "avatar_url": "https://cdn.example.com/avatars/alpha.png",
            "color": "#F472B6",
            "hidden": False,
            "tags": [{"prefix": "a:", "suffix": None}],
        },
        {
            "name": "Beta",
            "display_name": None,
            "avatar_url": None,
            "color": None,
            "hidden": True,
            "tags": [
                {"prefix": None, "suffix": "-b"},
                {"prefix": "b(", "suffix": ")"},
            ],
        },
    ]


def _post_file(
    client: httpx.Client,
    payload: bytes,
    *,
    idem_key: str | None = None,
    options: dict | None = None,
) -> dict:
    form: dict[str, str] = {
        "source": "masquerade_file",
        "idempotency_key": idem_key or str(uuid.uuid4()),
    }
    if options is not None:
        form["options"] = json.dumps(options)
    resp = client.post(
        "/v1/imports/file",
        files={"file": ("import.json", payload, "application/json")},
        data=form,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


# --- Preview --------------------------------------------------------------


def test_preview_returns_summary(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/import/masquerade/preview",
        files={
            "file": (
                "export.json",
                _masquerade_payload(_sample_profiles()),
                "application/json",
            )
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["member_count"] == 2
    names = {m["name"] for m in body["members"]}
    assert names == {"Alpha", "Beta"}


def test_preview_rejects_non_masquerade_shapes(auth_client: httpx.Client):
    """A non-object top level and an object without a `profiles` array are
    both refused - neither is a Masquerade export."""
    for payload in (b"[1, 2, 3]", json.dumps({"members": []}).encode()):
        resp = auth_client.post(
            "/v1/import/masquerade/preview",
            files={"file": ("export.json", payload, "application/json")},
        )
        assert resp.status_code == 400, resp.text


# --- Runner ---------------------------------------------------------------


def test_runner_imports_profile_fields(auth_client: httpx.Client):
    """Name, display name, external avatar, and colour land; proxy tags and
    the hidden flag are dropped without an error event, and the hidden
    profile arrives exactly as private as the visible one."""
    job = _post_file(auth_client, _masquerade_payload(_sample_profiles()))
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 2, final["counts"]
    assert not any(e["level"] == "error" for e in final["events"]), final["events"]

    by_name = {m["name"]: m for m in auth_client.get("/v1/members").json()}
    assert {"Alpha", "Beta"}.issubset(by_name.keys())
    alpha = by_name["Alpha"]
    assert alpha["display_name"] == "The Alpha", alpha
    assert alpha["avatar_url"] == "https://cdn.example.com/avatars/alpha.png", alpha
    assert alpha["color"] == "#f472b6", alpha
    # `hidden` maps to nothing: both members come in with the same default
    # private privacy, so the flag's drop is behaviour-neutral.
    assert alpha["privacy"] == "private", alpha
    assert by_name["Beta"]["privacy"] == "private", by_name["Beta"]


def test_runner_skips_nameless_profiles_with_warning(auth_client: httpx.Client):
    """A profile with a missing or non-string name is skipped with a tallied
    warning; the rest of the file still imports."""
    profiles = [
        {"name": "Gamma"},
        {"display_name": "no name here"},
        {"name": 12345},
    ]
    job = _post_file(auth_client, _masquerade_payload(profiles))
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 1, final["counts"]
    assert any(
        "no name" in e["message"] and e["level"] == "warning"
        for e in final["events"]
    ), final["events"]
    # No member content in the event log.
    blob = " ".join(e["message"] for e in final["events"])
    assert "Gamma" not in blob, blob


def test_runner_fails_on_non_object_payload(auth_client: httpx.Client):
    job = _post_file(auth_client, b"[1, 2, 3]")
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any(
        "JSON object" in e["message"] and e["level"] == "error"
        for e in final["events"]
    ), final["events"]


def test_runner_fails_when_profiles_not_a_list(auth_client: httpx.Client):
    job = _post_file(auth_client, json.dumps({"profiles": {"a": 1}}).encode())
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any(
        "profiles" in e["message"] and e["level"] == "error"
        for e in final["events"]
    ), final["events"]


def test_member_cap_fails_job_before_writing(auth_client: httpx.Client):
    """A 2-profile export against a 1-member cap fails cleanly and imports
    nothing."""
    set_member_limit(auth_client, 1)
    job = _post_file(auth_client, _masquerade_payload(_sample_profiles()))
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("limited to" in e["message"] for e in final["events"]), final["events"]
    members = auth_client.get("/v1/members").json()
    assert members == [], members


def test_avatar_with_bad_scheme_is_dropped(auth_client: httpx.Client):
    """A crafted export carrying a javascript: avatar URL must not land in
    the profile field."""
    profiles = [{"name": "Alpha", "avatar_url": "javascript:alert(1)"}]
    job = _post_file(auth_client, _masquerade_payload(profiles))
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    alpha = next(
        m for m in auth_client.get("/v1/members").json() if m["name"] == "Alpha"
    )
    assert alpha["avatar_url"] is None, alpha


def test_avatar_pointing_at_our_own_storage_is_dropped(auth_client: httpx.Client):
    """An internal serve-path ref in `avatar_url` is not an external URL,
    whatever the export claims - stored as-is it would re-sign into a live
    cross-tenant read of another account's upload."""
    victim_key = "avatars/22222222-2222-2222-2222-222222222222/stolen.png"
    profiles = [{"name": "Alpha", "avatar_url": f"/v1/files/{victim_key}"}]
    job = _post_file(auth_client, _masquerade_payload(profiles))
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    alpha = next(
        m for m in auth_client.get("/v1/members").json() if m["name"] == "Alpha"
    )
    assert not alpha["avatar_url"], alpha


def test_reimport_is_idempotent(auth_client: httpx.Client):
    """Re-importing the same file under the default skip strategy adds no
    duplicate members."""
    payload = _masquerade_payload(_sample_profiles())

    first = _post_file(auth_client, payload)
    drive_import_runner()
    f1 = wait_for_terminal(auth_client, first["id"])
    assert f1["status"] == "complete", f1
    assert f1["counts"]["members_imported"] == 2, f1["counts"]

    second = _post_file(auth_client, payload)
    drive_import_runner()
    f2 = wait_for_terminal(auth_client, second["id"])
    assert f2["status"] == "complete", f2
    assert f2["counts"].get("members_imported", 0) == 0, f2["counts"]
    assert f2["counts"].get("members_skipped", 0) == 2, f2["counts"]
    assert len(auth_client.get("/v1/members").json()) == 2


def test_member_selection_by_profile_position(auth_client: httpx.Client):
    """member_ids carries profile positions (the preview's ids), so a
    deselected profile stays out."""
    job = _post_file(
        auth_client,
        _masquerade_payload(_sample_profiles()),
        options={"member_ids": ["1"]},
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 1, final["counts"]
    names = {m["name"] for m in auth_client.get("/v1/members").json()}
    assert names == {"Beta"}, names
