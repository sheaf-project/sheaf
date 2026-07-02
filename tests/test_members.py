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


def test_bio_revision_list_pagination_walks_all(auth_client: httpx.Client):
    """The member bio-revision list is keyset-paginated, matching the journal /
    message / front-audit revision lists: a small `limit` truncates the page and
    signals more via X-Sheaf-Has-More / X-Sheaf-Next-Cursor, following the cursor
    walks every revision exactly once, and the body stays a plain array."""
    mid = auth_client.post(
        "/v1/members", json={"name": "Bio", "description": "v0"}
    ).json()["id"]
    # Five bio (description) edits -> five outgoing revisions captured.
    for i in range(1, 6):
        r = auth_client.patch(f"/v1/members/{mid}", json={"description": f"v{i}"})
        assert r.status_code == 200, r.text

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # generous loop bound
        params: dict[str, str] = {"limit": "2"}
        if cursor:
            params["cursor"] = cursor
        resp = auth_client.get(f"/v1/members/{mid}/revisions", params=params)
        assert resp.status_code == 200, resp.text
        page = resp.json()
        assert isinstance(page, list) and len(page) <= 2
        seen.extend(row["id"] for row in page)
        if resp.headers["X-Sheaf-Has-More"] != "true":
            break
        cursor = resp.headers["X-Sheaf-Next-Cursor"]

    assert len(seen) == 5, seen
    assert len(seen) == len(set(seen)), "page boundary produced a duplicate"


def test_bio_revision_list_rejects_bad_cursor(auth_client: httpx.Client):
    mid = auth_client.post(
        "/v1/members", json={"name": "BadCursor", "description": "x"}
    ).json()["id"]
    resp = auth_client.get(
        f"/v1/members/{mid}/revisions", params={"cursor": "not-a-cursor"}
    )
    assert resp.status_code == 400, resp.text
