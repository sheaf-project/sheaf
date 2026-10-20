"""Integration tests for the slider custom-field type.

Options validation at create and update, value validation on the
set-member-fields endpoint, and the public reading ("62%", "7.5 kg") a
share view serves for it. The option rules themselves are pinned in
tests/test_slider_fields_unit.py; these check the API wires them in.
"""

from __future__ import annotations

import uuid

import httpx


def _member(auth_client: httpx.Client, name: str = "Slidey") -> str:
    resp = auth_client.post("/v1/members", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_create_slider_without_options_gets_the_labeled_defaults(
    auth_client: httpx.Client,
):
    resp = auth_client.post("/v1/fields", json={"name": "Energy", "field_type": "slider"})
    assert resp.status_code == 201, resp.text
    field = resp.json()
    assert field["field_type"] == "slider"
    assert field["options"] == {
        "mode": "labeled",
        "min": 0,
        "max": 100,
        "step": 1,
        "unit": None,
        "labels": {"left": None, "center": None, "right": None},
        "gradient": None,
    }


def test_create_numeric_slider_with_gradient_round_trips(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/fields",
        json={
            "name": "Height",
            "field_type": "slider",
            "options": {
                "mode": "numeric",
                "min": 120,
                "max": 220,
                "step": 0.5,
                "unit": "cm",
                "gradient": ["#5b92c9", "#e08d5e"],
            },
        },
    )
    assert resp.status_code == 201, resp.text
    opts = resp.json()["options"]
    assert opts["mode"] == "numeric"
    assert (opts["min"], opts["max"], opts["step"], opts["unit"]) == (120, 220, 0.5, "cm")
    assert opts["gradient"] == ["#5B92C9", "#E08D5E"]

    listed = auth_client.get("/v1/fields").json()
    assert any(f["name"] == "Height" and f["options"]["unit"] == "cm" for f in listed)


def test_bad_slider_options_are_a_422(auth_client: httpx.Client):
    for options in (
        {"mode": "dial"},
        {"mode": "numeric", "min": 10, "max": 1},
        {"gradient": ["#FFF", "#000"]},
        {"labels": {"top": "x"}},
    ):
        resp = auth_client.post(
            "/v1/fields",
            json={"name": "Bad", "field_type": "slider", "options": options},
        )
        assert resp.status_code == 422, (options, resp.text)


def test_update_slider_options(auth_client: httpx.Client):
    create = auth_client.post("/v1/fields", json={"name": "Mood", "field_type": "slider"})
    field_id = create.json()["id"]
    patch = auth_client.patch(
        f"/v1/fields/{field_id}",
        json={"options": {"mode": "labeled", "labels": {"left": "Low", "right": "High"}}},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["options"]["labels"] == {"left": "Low", "center": None, "right": "High"}


def test_slider_values_are_numbers_inside_the_range(auth_client: httpx.Client):
    field_id = auth_client.post(
        "/v1/fields",
        json={
            "name": "Stars",
            "field_type": "slider",
            "options": {"mode": "numeric", "min": 1, "max": 5, "step": 1},
        },
    ).json()["id"]
    member_id = _member(auth_client)

    ok = auth_client.put(
        f"/v1/members/{member_id}/fields",
        json=[{"field_id": field_id, "value": 4}],
    )
    assert ok.status_code == 200, ok.text
    stored = auth_client.get(f"/v1/members/{member_id}/fields").json()
    assert [v["value"] for v in stored if v["field_id"] == field_id] == [4]

    # A bad value is a 400 with the field named, the same as a select value
    # that is not one of the choices (tests/test_custom_field_select.py).
    for bad in (0, 6, "4", True):
        resp = auth_client.put(
            f"/v1/members/{member_id}/fields",
            json=[{"field_id": field_id, "value": bad}],
        )
        assert resp.status_code == 400, (bad, resp.text)
        assert "Stars" in resp.json()["detail"], resp.text

    # A float inside the range is fine even on a whole-step slider: the step
    # is a hint for the control, not a storage constraint.
    resp = auth_client.put(
        f"/v1/members/{member_id}/fields",
        json=[{"field_id": field_id, "value": 2.5}],
    )
    assert resp.status_code == 200, resp.text

    # null clears, as for every other type.
    cleared = auth_client.put(
        f"/v1/members/{member_id}/fields",
        json=[{"field_id": field_id, "value": None}],
    )
    assert cleared.status_code == 200, cleared.text
    after = auth_client.get(f"/v1/members/{member_id}/fields").json()
    assert not [v for v in after if v["field_id"] == field_id and v["value"] is not None]


# --- Export and import coverage ---------------------------------------------


def _fresh_account() -> httpx.Client:
    from tests.conftest import BASE_URL

    c = httpx.Client(base_url=BASE_URL)
    r = c.post(
        "/v1/auth/register",
        json={"email": f"slider-{uuid.uuid4().hex[:8]}@sheaf.dev", "password": "testpassword123"},
    )
    assert r.status_code == 201, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


_HEIGHT_OPTIONS = {
    "mode": "numeric",
    "min": 120,
    "max": 220,
    "step": 0.5,
    "unit": "cm",
    "labels": {"left": None, "center": None, "right": None},
    "gradient": ["#5B92C9", "#E08D5E"],
}


def _slider_system(c: httpx.Client) -> tuple[str, str]:
    field_id = c.post(
        "/v1/fields",
        json={"name": "Height", "field_type": "slider", "options": _HEIGHT_OPTIONS},
    ).json()["id"]
    member_id = _member(c, "Tall")
    r = c.put(f"/v1/members/{member_id}/fields", json=[{"field_id": field_id, "value": 172.5}])
    assert r.status_code == 200, r.text
    return field_id, member_id


def test_slider_fields_round_trip_through_the_native_export(auth_client: httpx.Client):
    """Definition (type + full options) and the numeric value survive
    /v1/export and a re-import into another account, the same as any other
    field type. The Article 15 account-data export is system-content-free
    by design (see sheaf/api/v1/account.py) and so carries no fields."""
    from tests._import_runner_helpers import drive_import_runner, wait_for_terminal

    _slider_system(auth_client)
    export = auth_client.get("/v1/export")
    assert export.status_code == 200, export.text
    fields = [f for f in export.json()["custom_fields"] if f["name"] == "Height"]
    assert len(fields) == 1, export.json()["custom_fields"]
    assert fields[0]["field_type"] == "slider"
    assert fields[0]["options"] == _HEIGHT_OPTIONS
    assert [v["value"] for v in fields[0]["values"]] == [172.5]

    other = _fresh_account()
    resp = other.post(
        "/v1/imports/file",
        files={"file": ("sheaf.json", export.content, "application/json")},
        data={"source": "sheaf_file", "idempotency_key": str(uuid.uuid4())},
    )
    assert resp.status_code == 202, resp.text
    drive_import_runner()
    final = wait_for_terminal(other, resp.json()["id"])
    assert final["status"] == "complete", final

    imported = {f["name"]: f for f in other.get("/v1/fields").json()}
    assert imported["Height"]["field_type"] == "slider"
    assert imported["Height"]["options"] == _HEIGHT_OPTIONS
    member = next(m for m in other.get("/v1/members").json() if m["name"] == "Tall")
    values = other.get(f"/v1/members/{member['id']}/fields").json()
    assert [v["value"] for v in values if v["field_id"] == imported["Height"]["id"]] == [172.5]
    other.close()


def test_slider_fields_are_carried_by_the_pluralport_export(auth_client: httpx.Client):
    """PluralPort passes the type and options through unchanged, so a slider
    definition is intact on the way out and lands back as a slider when the
    file is imported (it goes through the native importer's normaliser)."""
    _slider_system(auth_client)
    export = auth_client.get("/v1/export", params={"format": "pluralport"})
    assert export.status_code == 200, export.text
    body = export.json()
    fields = [f for f in body["custom_fields"] if f["name"] == "Height"]
    assert len(fields) == 1, body["custom_fields"]
    assert fields[0]["field_type"] == "slider"
    assert fields[0]["options"] == _HEIGHT_OPTIONS
    values = [v for v in body["custom_field_values"] if v["field_id"] == fields[0]["id"]]
    assert [v["value"] for v in values] == [172.5]
