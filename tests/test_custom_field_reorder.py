"""Behavioural tests for PUT /v1/fields/reorder.

The mirror of the group reorder tests in test_groups_tags.py: same body
shape, same validation, same "rows not named keep the order they had"
contract. Field lists sort by order alone (no name tiebreak), so these
tests always leave every field with a distinct order before asserting on
list positions.
"""

from __future__ import annotations

import uuid

import httpx


def _create_field(client: httpx.Client, name: str, order: int = 0) -> str:
    resp = client.post(
        "/v1/fields",
        json={"name": name, "field_type": "text", "order": order},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _field_names(client: httpx.Client) -> list[str]:
    return [f["name"] for f in client.get("/v1/fields").json()]


def test_reorder_fields(auth_client: httpx.Client):
    ids = {
        name: _create_field(auth_client, name)
        for name in ("Alpha", "Beta", "Gamma")
    }
    resp = auth_client.put(
        "/v1/fields/reorder",
        json={"field_ids": [ids["Gamma"], ids["Alpha"], ids["Beta"]]},
    )
    assert resp.status_code == 200, resp.text
    # The response IS the re-sorted list, and the list endpoint agrees.
    assert [f["name"] for f in resp.json()] == ["Gamma", "Alpha", "Beta"]
    assert _field_names(auth_client) == ["Gamma", "Alpha", "Beta"]


def test_reorder_fields_unnamed_keep_their_order(auth_client: httpx.Client):
    ids = {
        name: _create_field(auth_client, name, order=i)
        for i, name in enumerate(("Alpha", "Beta", "Gamma"))
    }
    # Naming only Beta and Alpha reorders that pair; Gamma keeps order 2.
    resp = auth_client.put(
        "/v1/fields/reorder",
        json={"field_ids": [ids["Beta"], ids["Alpha"]]},
    )
    assert resp.status_code == 200, resp.text
    assert _field_names(auth_client) == ["Beta", "Alpha", "Gamma"]


def test_reorder_fields_duplicate_ids_rejected(auth_client: httpx.Client):
    fid = _create_field(auth_client, "Dup")
    resp = auth_client.put(
        "/v1/fields/reorder", json={"field_ids": [fid, fid]}
    )
    assert resp.status_code == 400


def test_reorder_fields_rejects_unknown_and_foreign_ids(auth_client: httpx.Client):
    mine_a = _create_field(auth_client, "KeepA", order=0)
    mine_b = _create_field(auth_client, "KeepB", order=1)

    resp = auth_client.put(
        "/v1/fields/reorder",
        json={"field_ids": [mine_a, str(uuid.uuid4())]},
    )
    assert resp.status_code == 400

    # A real field on another account gets the exact same answer, with
    # nothing in the response hinting which id was the problem.
    with httpx.Client(base_url=str(auth_client.base_url)) as other:
        reg = other.post(
            "/v1/auth/register",
            json={
                "email": f"test-{uuid.uuid4().hex[:8]}@sheaf.dev",
                "password": "testpassword123",
            },
        )
        other.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        foreign = other.post(
            "/v1/fields", json={"name": "NotYours", "field_type": "text"}
        ).json()["id"]

    resp = auth_client.put(
        "/v1/fields/reorder",
        json={"field_ids": [mine_b, mine_a, foreign]},
    )
    assert resp.status_code == 400
    assert foreign not in resp.text

    # The refused call moved nothing.
    assert _field_names(auth_client) == ["KeepA", "KeepB"]
