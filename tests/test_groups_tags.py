import httpx


def _create_member(client: httpx.Client, name: str) -> str:
    resp = client.post("/v1/members", json={"name": name})
    return resp.json()["id"]


def test_create_group(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/groups", json={"name": "Protectors", "color": "#cc0000"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Protectors"


def test_nested_groups(auth_client: httpx.Client):
    parent = auth_client.post("/v1/groups", json={"name": "Parent"})
    parent_id = parent.json()["id"]

    child = auth_client.post(
        "/v1/groups", json={"name": "Child", "parent_id": parent_id},
    )
    assert child.status_code == 201
    assert child.json()["parent_id"] == parent_id


def test_group_members(auth_client: httpx.Client):
    m1 = _create_member(auth_client, "GroupMem1")
    m2 = _create_member(auth_client, "GroupMem2")
    group = auth_client.post("/v1/groups", json={"name": "TestGroup"})
    group_id = group.json()["id"]

    resp = auth_client.put(
        f"/v1/groups/{group_id}/members",
        json={"member_ids": [m1, m2]},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp = auth_client.get(f"/v1/groups/{group_id}/members")
    assert len(resp.json()) == 2


def test_create_tag(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/tags", json={"name": "frequent", "color": "#00ff00"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "frequent"


def test_tag_crud(auth_client: httpx.Client):
    resp = auth_client.post("/v1/tags", json={"name": "to-update"})
    tag_id = resp.json()["id"]

    resp = auth_client.patch(
        f"/v1/tags/{tag_id}",
        json={"name": "updated-tag", "color": "#abcdef"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "updated-tag"

    resp = auth_client.delete(f"/v1/tags/{tag_id}")
    assert resp.status_code == 204

    resp = auth_client.get(f"/v1/tags/{tag_id}")
    assert resp.status_code == 404
