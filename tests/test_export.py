import httpx


def test_export_empty_system(auth_client: httpx.Client):
    resp = auth_client.get("/v1/export")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "1"
    assert data["system"]["name"] == "My System"
    assert data["members"] == []
    assert data["fronts"] == []


def test_export_with_data(auth_client: httpx.Client):
    # Create a member
    member_resp = auth_client.post(
        "/v1/members", json={"name": "ExportMember", "pronouns": "they/them"},
    )
    member_id = member_resp.json()["id"]

    # Create a front
    auth_client.post("/v1/fronts", json={"member_ids": [member_id]})

    # Create a group with the member
    group_resp = auth_client.post("/v1/groups", json={"name": "ExportGroup"})
    group_id = group_resp.json()["id"]
    auth_client.put(
        f"/v1/groups/{group_id}/members", json={"member_ids": [member_id]},
    )

    # Create a tag
    auth_client.post("/v1/tags", json={"name": "export-tag"})

    # Export
    resp = auth_client.get("/v1/export")
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["members"]) == 1
    assert data["members"][0]["name"] == "ExportMember"
    assert len(data["fronts"]) == 1
    assert member_id in data["fronts"][0]["member_ids"]
    assert len(data["groups"]) == 1
    assert member_id in data["groups"][0]["member_ids"]
    assert len(data["tags"]) == 1
