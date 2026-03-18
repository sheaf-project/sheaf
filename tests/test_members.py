import httpx


def test_create_member(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/members",
        json={"name": "Alice", "pronouns": "she/her", "color": "#ff6b9d"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Alice"
    assert data["pronouns"] == "she/her"
    assert data["color"] == "#ff6b9d"
    assert data["privacy"] == "private"


def test_list_members(auth_client: httpx.Client):
    auth_client.post("/v1/members", json={"name": "Zara"})
    auth_client.post("/v1/members", json={"name": "Alex"})
    resp = auth_client.get("/v1/members")
    assert resp.status_code == 200
    names = [m["name"] for m in resp.json()]
    assert names == sorted(names)


def test_update_member(auth_client: httpx.Client):
    resp = auth_client.post("/v1/members", json={"name": "Original"})
    member_id = resp.json()["id"]

    resp = auth_client.patch(
        f"/v1/members/{member_id}",
        json={"name": "Updated", "pronouns": "they/them"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated"
    assert resp.json()["pronouns"] == "they/them"


def test_delete_member(auth_client: httpx.Client):
    resp = auth_client.post("/v1/members", json={"name": "ToDelete"})
    member_id = resp.json()["id"]

    resp = auth_client.delete(f"/v1/members/{member_id}")
    assert resp.status_code == 204

    resp = auth_client.get(f"/v1/members/{member_id}")
    assert resp.status_code == 404


def test_get_nonexistent_member(auth_client: httpx.Client):
    resp = auth_client.get("/v1/members/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
