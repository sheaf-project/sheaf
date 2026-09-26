"""GET /v1/fronts/current/compact - the fronter view for constrained clients.

The shape assertions here are not cosmetic. Some embedded HTTP clients
cannot consume a top-level JSON array: Connect IQ, for one, types its
response payload as Dictionary/String/Iterator/Null, so a multi-element
array arrives as null even alongside a 200. Returning a bare array here
would break those clients silently, with a success status and no body.
"""

import httpx


def _create_member(client: httpx.Client, name: str, display_name: str | None = None) -> str:
    body: dict[str, object] = {"name": name}
    if display_name is not None:
        body["display_name"] = display_name
    resp = client.post("/v1/members", json=body)
    return resp.json()["id"]


def _compact(client: httpx.Client) -> dict:
    resp = client.get("/v1/fronts/current/compact")
    assert resp.status_code == 200
    return resp.json()


def _by_id(payload: dict, member_id: str) -> dict | None:
    return next((f for f in payload["fronters"] if f["id"] == member_id), None)


def test_compact_response_is_an_object_not_an_array(auth_client: httpx.Client):
    """The wrapper is the entire point of this endpoint. See module docstring."""
    member_id = _create_member(auth_client, "CompactShape")
    auth_client.post("/v1/fronts", json={"member_ids": [member_id]})

    body = _compact(auth_client)
    assert isinstance(body, dict)
    assert isinstance(body["fronters"], list)


def test_compact_lists_current_fronter_with_name_and_since(auth_client: httpx.Client):
    member_id = _create_member(auth_client, "CompactNamed")
    auth_client.post("/v1/fronts", json={"member_ids": [member_id]})

    row = _by_id(_compact(auth_client), member_id)
    assert row is not None
    assert row["name"] == "CompactNamed"
    assert row["since"]


def test_compact_prefers_display_name(auth_client: httpx.Client):
    member_id = _create_member(auth_client, "CompactReal", display_name="Compact Shown")
    auth_client.post("/v1/fronts", json={"member_ids": [member_id]})

    row = _by_id(_compact(auth_client), member_id)
    assert row is not None
    assert row["name"] == "Compact Shown"


def test_compact_flattens_members_of_a_co_front(auth_client: httpx.Client):
    m1 = _create_member(auth_client, "CompactCo1")
    m2 = _create_member(auth_client, "CompactCo2")
    auth_client.post("/v1/fronts", json={"member_ids": [m1, m2]})

    body = _compact(auth_client)
    assert _by_id(body, m1) is not None
    assert _by_id(body, m2) is not None


def test_compact_covers_several_separate_open_fronts(auth_client: httpx.Client):
    """The case that motivated the endpoint: more than one open front entry.

    Two members added as separate entries rather than one co-front, which is
    what the full /current view renders as a multi-element top-level array.
    """
    m1 = _create_member(auth_client, "CompactSolo1")
    m2 = _create_member(auth_client, "CompactSolo2")
    auth_client.post("/v1/fronts", json={"member_ids": [m1], "replace_fronts": False})
    auth_client.post("/v1/fronts", json={"member_ids": [m2], "replace_fronts": False})

    body = _compact(auth_client)
    assert _by_id(body, m1) is not None
    assert _by_id(body, m2) is not None


def test_compact_lists_each_member_once(auth_client: httpx.Client):
    m1 = _create_member(auth_client, "CompactDedupe1")
    m2 = _create_member(auth_client, "CompactDedupe2")
    auth_client.post("/v1/fronts", json={"member_ids": [m1], "replace_fronts": False})
    auth_client.post("/v1/fronts", json={"member_ids": [m1, m2], "replace_fronts": False})

    ids = [f["id"] for f in _compact(auth_client)["fronters"]]
    assert ids.count(m1) == 1


def test_compact_omits_ended_fronts(auth_client: httpx.Client):
    member_id = _create_member(auth_client, "CompactEnded")
    created = auth_client.post("/v1/fronts", json={"member_ids": [member_id]})
    front_id = created.json()["id"]
    auth_client.delete(f"/v1/fronts/{front_id}")

    assert _by_id(_compact(auth_client), member_id) is None
