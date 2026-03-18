import httpx


def _create_member(client: httpx.Client, name: str) -> str:
    resp = client.post("/v1/members", json={"name": name})
    return resp.json()["id"]


def test_create_front(auth_client: httpx.Client):
    member_id = _create_member(auth_client, "Fronter")
    resp = auth_client.post("/v1/fronts", json={"member_ids": [member_id]})
    assert resp.status_code == 201
    data = resp.json()
    assert data["member_ids"] == [member_id]
    assert data["ended_at"] is None


def test_co_front(auth_client: httpx.Client):
    m1 = _create_member(auth_client, "CoFront1")
    m2 = _create_member(auth_client, "CoFront2")
    resp = auth_client.post("/v1/fronts", json={"member_ids": [m1, m2]})
    assert resp.status_code == 201
    assert set(resp.json()["member_ids"]) == {m1, m2}


def test_current_fronts(auth_client: httpx.Client):
    member_id = _create_member(auth_client, "Current")
    auth_client.post("/v1/fronts", json={"member_ids": [member_id]})
    resp = auth_client.get("/v1/fronts/current")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
    assert all(f["ended_at"] is None for f in resp.json())


def test_end_front(auth_client: httpx.Client):
    member_id = _create_member(auth_client, "Ender")
    resp = auth_client.post("/v1/fronts", json={"member_ids": [member_id]})
    front_id = resp.json()["id"]

    resp = auth_client.patch(
        f"/v1/fronts/{front_id}",
        json={"ended_at": "2026-03-18T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert resp.json()["ended_at"] is not None


def test_invalid_member_in_front(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/fronts",
        json={"member_ids": ["00000000-0000-0000-0000-000000000000"]},
    )
    assert resp.status_code == 400


def test_front_history_pagination(auth_client: httpx.Client):
    member_id = _create_member(auth_client, "Paginated")
    for _ in range(3):
        auth_client.post("/v1/fronts", json={"member_ids": [member_id]})
    resp = auth_client.get("/v1/fronts", params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2
