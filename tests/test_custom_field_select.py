"""Integration tests for the select / multiselect custom-field types.

Covers options validation at create + update time, and per-value
validation on the set-member-fields endpoint. The plain text/number/
date/boolean types are covered indirectly by the existing test
suites — those types are unchanged here.
"""

from __future__ import annotations

import httpx

# ---------------------------------------------------------------------------
# Field definition (options) validation
# ---------------------------------------------------------------------------

def test_create_select_field_without_options_is_freeform(auth_client: httpx.Client):
    """Mobile clients currently pass options=null. The server accepts
    that as "freeform tag" mode — values are stored as-is, no choices
    constraint is enforced."""
    resp = auth_client.post(
        "/v1/fields",
        json={"name": "Species", "field_type": "select"},
    )
    assert resp.status_code == 201, resp.text
    field = resp.json()
    assert field["field_type"] == "select"
    assert field["options"] is None


def test_create_select_with_choices_normalises_and_persists(auth_client: httpx.Client):
    """When choices are supplied they get trimmed, deduped (case
    insensitive), and stored back on the field."""
    resp = auth_client.post(
        "/v1/fields",
        json={
            "name": "Pronouns set",
            "field_type": "select",
            "options": {
                "choices": ["she/her", " they/them ", "She/Her", "he/him"]
            },
        },
    )
    assert resp.status_code == 201, resp.text
    field = resp.json()
    # Order preserved, second "She/Her" deduped against the leading
    # "she/her", trailing whitespace stripped on "they/them".
    assert field["options"] == {"choices": ["she/her", "they/them", "he/him"]}


def test_create_select_with_empty_choices_rejected(auth_client: httpx.Client):
    """An options dict with an empty choices list is a 422 — if the
    caller wanted freeform mode they'd omit options entirely."""
    resp = auth_client.post(
        "/v1/fields",
        json={
            "name": "Empty select",
            "field_type": "select",
            "options": {"choices": []},
        },
    )
    assert resp.status_code == 422, resp.text


def test_options_rejected_for_non_select_types(auth_client: httpx.Client):
    """Text / number / date / boolean don't carry options; sending
    them is a validation error so a typo can't silently get persisted."""
    resp = auth_client.post(
        "/v1/fields",
        json={
            "name": "Age",
            "field_type": "number",
            "options": {"choices": ["x"]},
        },
    )
    assert resp.status_code == 422, resp.text


def test_update_select_options_normalises(auth_client: httpx.Client):
    """PATCH options also runs through the normaliser."""
    field = auth_client.post(
        "/v1/fields",
        json={"name": "Role", "field_type": "select"},
    ).json()
    resp = auth_client.patch(
        f"/v1/fields/{field['id']}",
        json={"options": {"choices": ["lead", "lead", "support"]}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["options"] == {"choices": ["lead", "support"]}


# ---------------------------------------------------------------------------
# Member value validation
# ---------------------------------------------------------------------------

def _member(client: httpx.Client, name: str = "Alice") -> str:
    resp = client.post("/v1/members", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_select_value_must_match_choices(auth_client: httpx.Client):
    member = _member(auth_client)
    field = auth_client.post(
        "/v1/fields",
        json={
            "name": "Status",
            "field_type": "select",
            "options": {"choices": ["active", "asleep"]},
        },
    ).json()

    # Valid choice -> 200
    resp = auth_client.put(
        f"/v1/members/{member}/fields",
        json=[{"field_id": field["id"], "value": "active"}],
    )
    assert resp.status_code == 200, resp.text

    # Off-choice -> 400 with a helpful message
    resp = auth_client.put(
        f"/v1/members/{member}/fields",
        json=[{"field_id": field["id"], "value": "nope"}],
    )
    assert resp.status_code == 400, resp.text
    assert "not one of the defined choices" in resp.json()["detail"]


def test_select_freeform_accepts_anything(auth_client: httpx.Client):
    """When choices are unset (mobile's current shape), any string is
    accepted as a value."""
    member = _member(auth_client)
    field = auth_client.post(
        "/v1/fields",
        json={"name": "Tags", "field_type": "select"},
    ).json()
    resp = auth_client.put(
        f"/v1/members/{member}/fields",
        json=[{"field_id": field["id"], "value": "arbitrary"}],
    )
    assert resp.status_code == 200, resp.text


def test_multiselect_value_validates_each_entry(auth_client: httpx.Client):
    member = _member(auth_client)
    field = auth_client.post(
        "/v1/fields",
        json={
            "name": "Roles",
            "field_type": "multiselect",
            "options": {"choices": ["lead", "support", "rest"]},
        },
    ).json()

    # All in choices, no dupes -> 200
    resp = auth_client.put(
        f"/v1/members/{member}/fields",
        json=[
            {"field_id": field["id"], "value": ["lead", "support"]},
        ],
    )
    assert resp.status_code == 200, resp.text

    # Off-choice in the list -> 400
    resp = auth_client.put(
        f"/v1/members/{member}/fields",
        json=[
            {"field_id": field["id"], "value": ["lead", "stranger"]},
        ],
    )
    assert resp.status_code == 400, resp.text

    # Duplicate entry -> 400
    resp = auth_client.put(
        f"/v1/members/{member}/fields",
        json=[
            {"field_id": field["id"], "value": ["lead", "lead"]},
        ],
    )
    assert resp.status_code == 400, resp.text
    assert "more than once" in resp.json()["detail"]


def test_multiselect_empty_list_clears_selection(auth_client: httpx.Client):
    """An empty list is a legitimate value — clears any previous
    selection without violating the choices constraint."""
    member = _member(auth_client)
    field = auth_client.post(
        "/v1/fields",
        json={
            "name": "Tags",
            "field_type": "multiselect",
            "options": {"choices": ["a", "b"]},
        },
    ).json()
    resp = auth_client.put(
        f"/v1/members/{member}/fields",
        json=[{"field_id": field["id"], "value": []}],
    )
    assert resp.status_code == 200, resp.text


def test_legacy_envelope_value_still_validated(auth_client: httpx.Client):
    """The web client wraps submitted values as `{v: <scalar>}` for
    historical reasons. The validator unwraps once before checking
    constraints so the envelope and the raw form are equivalent."""
    member = _member(auth_client)
    field = auth_client.post(
        "/v1/fields",
        json={
            "name": "Status",
            "field_type": "select",
            "options": {"choices": ["active", "asleep"]},
        },
    ).json()

    resp = auth_client.put(
        f"/v1/members/{member}/fields",
        json=[{"field_id": field["id"], "value": {"v": "active"}}],
    )
    assert resp.status_code == 200, resp.text

    resp = auth_client.put(
        f"/v1/members/{member}/fields",
        json=[{"field_id": field["id"], "value": {"v": "nope"}}],
    )
    assert resp.status_code == 400, resp.text
