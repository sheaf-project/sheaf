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


def test_group_depth_cap(auth_client: httpx.Client):
    # 8 nested levels are allowed; the 9th is rejected.
    parent_id: str | None = None
    for i in range(8):
        body: dict = {"name": f"Depth{i}"}
        if parent_id:
            body["parent_id"] = parent_id
        resp = auth_client.post("/v1/groups", json=body)
        assert resp.status_code == 201, resp.text
        parent_id = resp.json()["id"]

    resp = auth_client.post(
        "/v1/groups", json={"name": "TooDeep", "parent_id": parent_id}
    )
    assert resp.status_code == 400
    assert "levels" in resp.json()["detail"].lower()


def test_group_reparent_cycle_rejected(auth_client: httpx.Client):
    a = auth_client.post("/v1/groups", json={"name": "CycA"}).json()["id"]
    b = auth_client.post(
        "/v1/groups", json={"name": "CycB", "parent_id": a}
    ).json()["id"]
    # Making A a child of its own descendant B would form a cycle.
    resp = auth_client.patch(f"/v1/groups/{a}", json={"parent_id": b})
    assert resp.status_code == 400
    assert "circular" in resp.json()["detail"].lower()


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


# ---------------------------------------------------------------------------
# Tag membership (tag-side and member-side endpoints — symmetric m2m)
# ---------------------------------------------------------------------------


def test_set_tag_members(auth_client: httpx.Client):
    m1 = _create_member(auth_client, "TagMem1")
    m2 = _create_member(auth_client, "TagMem2")
    tag_id = auth_client.post("/v1/tags", json={"name": "creative"}).json()["id"]

    resp = auth_client.put(
        f"/v1/tags/{tag_id}/members",
        json={"member_ids": [m1, m2]},
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 2

    listed = auth_client.get(f"/v1/tags/{tag_id}/members")
    assert listed.status_code == 200
    assert {m["id"] for m in listed.json()} == {m1, m2}


def test_set_tag_members_replaces(auth_client: httpx.Client):
    """PUT semantics — body replaces the full set, not additive."""
    m1 = _create_member(auth_client, "TagReplace1")
    m2 = _create_member(auth_client, "TagReplace2")
    tag_id = auth_client.post("/v1/tags", json={"name": "label"}).json()["id"]

    auth_client.put(f"/v1/tags/{tag_id}/members", json={"member_ids": [m1, m2]})
    auth_client.put(f"/v1/tags/{tag_id}/members", json={"member_ids": [m1]})

    listed = auth_client.get(f"/v1/tags/{tag_id}/members").json()
    assert {m["id"] for m in listed} == {m1}


def test_set_tag_members_rejects_unknown_member(auth_client: httpx.Client):
    import uuid

    tag_id = auth_client.post("/v1/tags", json={"name": "bad"}).json()["id"]
    resp = auth_client.put(
        f"/v1/tags/{tag_id}/members",
        json={"member_ids": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 400


def test_set_tag_members_clears_with_empty_list(auth_client: httpx.Client):
    m1 = _create_member(auth_client, "TagClear1")
    tag_id = auth_client.post("/v1/tags", json={"name": "temp"}).json()["id"]

    auth_client.put(f"/v1/tags/{tag_id}/members", json={"member_ids": [m1]})
    auth_client.put(f"/v1/tags/{tag_id}/members", json={"member_ids": []})

    listed = auth_client.get(f"/v1/tags/{tag_id}/members").json()
    assert listed == []


def test_member_side_tag_endpoints(auth_client: httpx.Client):
    """The /v1/members/{id}/tags endpoint is the symmetric counterpart of
    /v1/tags/{id}/members. Setting from one side should be visible from
    the other."""
    member_id = _create_member(auth_client, "MemberTagSide")
    t1 = auth_client.post("/v1/tags", json={"name": "primary"}).json()["id"]
    t2 = auth_client.post("/v1/tags", json={"name": "creative"}).json()["id"]

    # Initially no tags.
    initial = auth_client.get(f"/v1/members/{member_id}/tags").json()
    assert initial == []

    # Set both via member-side PUT.
    resp = auth_client.put(
        f"/v1/members/{member_id}/tags",
        json={"tag_ids": [t1, t2]},
    )
    assert resp.status_code == 200, resp.text
    assert {t["id"] for t in resp.json()} == {t1, t2}

    # Visible from tag-side GET on either tag.
    t1_members = auth_client.get(f"/v1/tags/{t1}/members").json()
    assert any(m["id"] == member_id for m in t1_members)

    # Clear from member side.
    auth_client.put(
        f"/v1/members/{member_id}/tags", json={"tag_ids": []}
    )
    assert auth_client.get(f"/v1/members/{member_id}/tags").json() == []

    # Set from tag side; member-side GET picks it up.
    auth_client.put(
        f"/v1/tags/{t1}/members", json={"member_ids": [member_id]}
    )
    assert {t["id"] for t in auth_client.get(
        f"/v1/members/{member_id}/tags"
    ).json()} == {t1}


def test_member_side_tag_set_rejects_unknown_tag(auth_client: httpx.Client):
    import uuid

    member_id = _create_member(auth_client, "BadTagSet")
    resp = auth_client.put(
        f"/v1/members/{member_id}/tags",
        json={"tag_ids": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 400
