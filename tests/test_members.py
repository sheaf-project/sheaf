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


def test_archive_member_soft_hide(auth_client: httpx.Client):
    mid = auth_client.post("/v1/members", json={"name": "ToArchive"}).json()["id"]

    # Archive is ungated by default (no safety auth tier configured).
    resp = auth_client.post(f"/v1/members/{mid}/archive")
    assert resp.status_code == 200, resp.text
    assert resp.json()["archived_at"] is not None

    # Default list still includes archived (history must resolve names), flagged.
    listed = {x["id"]: x for x in auth_client.get("/v1/members").json()}
    assert mid in listed and listed[mid]["archived_at"] is not None

    # include_archived=false hides it; top-fronters excludes it.
    active = {x["id"] for x in auth_client.get(
        "/v1/members", params={"include_archived": False}
    ).json()}
    assert mid not in active
    top = {x["id"] for x in auth_client.get("/v1/members/top-fronters").json()}
    assert mid not in top

    # Unarchive restores it.
    resp = auth_client.post(f"/v1/members/{mid}/unarchive")
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is None
    active2 = {x["id"] for x in auth_client.get(
        "/v1/members", params={"include_archived": False}
    ).json()}
    assert mid in active2


def test_archive_member_idempotent(auth_client: httpx.Client):
    mid = auth_client.post("/v1/members", json={"name": "ArchiveTwice"}).json()["id"]
    first = auth_client.post(f"/v1/members/{mid}/archive").json()
    again = auth_client.post(f"/v1/members/{mid}/archive").json()
    # Re-archiving is a no-op; the timestamp does not move.
    assert first["archived_at"] == again["archived_at"]
