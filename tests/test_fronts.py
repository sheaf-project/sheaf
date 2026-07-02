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
    from datetime import UTC, datetime, timedelta

    member_id = _create_member(auth_client, "Ender")
    resp = auth_client.post("/v1/fronts", json={"member_ids": [member_id]})
    front_id = resp.json()["id"]

    end_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    resp = auth_client.patch(
        f"/v1/fronts/{front_id}",
        json={"ended_at": end_at},
    )
    assert resp.status_code == 200, resp.text
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


# --- Cursor pagination ------------------------------------------------------


def test_history_has_more_signals_when_truncated(auth_client: httpx.Client):
    """A `limit` smaller than the total set yields `X-Sheaf-Has-More: true`
    and a non-empty `X-Sheaf-Next-Cursor` header. Both surfaces are how
    callers know they're not seeing the whole list."""
    member_id = _create_member(auth_client, "HasMore")
    for _ in range(4):
        auth_client.post("/v1/fronts", json={"member_ids": [member_id]})
    resp = auth_client.get("/v1/fronts", params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    assert resp.headers["X-Sheaf-Has-More"] == "true"
    assert resp.headers.get("X-Sheaf-Next-Cursor")


def test_history_has_more_false_when_page_is_short(auth_client: httpx.Client):
    member_id = _create_member(auth_client, "Short")
    for _ in range(3):
        auth_client.post("/v1/fronts", json={"member_ids": [member_id]})
    resp = auth_client.get("/v1/fronts", params={"limit": 50})
    assert resp.status_code == 200
    assert len(resp.json()) == 3
    assert resp.headers["X-Sheaf-Has-More"] == "false"
    # No next-cursor when there's nothing further to fetch.
    assert "X-Sheaf-Next-Cursor" not in resp.headers


def test_history_exact_limit_boundary(auth_client: httpx.Client):
    """`limit == total` is the boundary case where the `+ 1` probe must
    not falsely advertise `has_more=true`."""
    member_id = _create_member(auth_client, "Boundary")
    for _ in range(3):
        auth_client.post("/v1/fronts", json={"member_ids": [member_id]})
    resp = auth_client.get("/v1/fronts", params={"limit": 3})
    assert resp.status_code == 200
    assert len(resp.json()) == 3
    assert resp.headers["X-Sheaf-Has-More"] == "false"
    assert "X-Sheaf-Next-Cursor" not in resp.headers


def test_history_cursor_paginates_through_all(auth_client: httpx.Client):
    """Walking pages via the returned cursor yields every entry exactly
    once, in the same order as a single big request."""
    member_id = _create_member(auth_client, "Walker")
    for _ in range(7):
        auth_client.post("/v1/fronts", json={"member_ids": [member_id]})

    # Reference: fetch everything in one go.
    all_resp = auth_client.get("/v1/fronts", params={"limit": 50})
    all_ids = [f["id"] for f in all_resp.json()]
    assert len(all_ids) == 7

    # Walk in pages of 3.
    collected: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"limit": 3}
        if cursor is not None:
            params["cursor"] = cursor
        resp = auth_client.get("/v1/fronts", params=params)
        assert resp.status_code == 200
        collected.extend(f["id"] for f in resp.json())
        pages += 1
        if resp.headers["X-Sheaf-Has-More"] != "true":
            break
        cursor = resp.headers["X-Sheaf-Next-Cursor"]
        assert pages < 10, "runaway pagination"

    assert collected == all_ids


def test_history_cursor_invalid_returns_400(auth_client: httpx.Client):
    resp = auth_client.get(
        "/v1/fronts", params={"limit": 5, "cursor": "not-a-real-cursor"}
    )
    assert resp.status_code == 400
    assert "cursor" in resp.json()["detail"].lower()


def test_history_total_count_opt_in(auth_client: httpx.Client):
    """`include_total=true` adds `X-Sheaf-Total-Count` (one extra COUNT
    query). Off by default so cursor / load-more callers don't pay."""
    member_id = _create_member(auth_client, "Counter")
    for _ in range(4):
        auth_client.post("/v1/fronts", json={"member_ids": [member_id]})

    off = auth_client.get("/v1/fronts", params={"limit": 2})
    assert "X-Sheaf-Total-Count" not in off.headers

    on = auth_client.get(
        "/v1/fronts", params={"limit": 2, "include_total": "true"}
    )
    assert on.status_code == 200
    assert on.headers["X-Sheaf-Total-Count"] == "4"


def test_history_cursor_takes_precedence_over_offset(auth_client: httpx.Client):
    """When both are sent, cursor wins. Offset is silently ignored — the
    response is the cursor's next page, not offset-from-start."""
    member_id = _create_member(auth_client, "BothParams")
    for _ in range(5):
        auth_client.post("/v1/fronts", json={"member_ids": [member_id]})

    first = auth_client.get("/v1/fronts", params={"limit": 2}).json()
    first_resp = auth_client.get("/v1/fronts", params={"limit": 2})
    cursor = first_resp.headers["X-Sheaf-Next-Cursor"]

    # cursor says "page 2"; offset=0 would say "page 1". Cursor wins.
    resp = auth_client.get(
        "/v1/fronts", params={"limit": 2, "cursor": cursor, "offset": 0}
    )
    assert resp.status_code == 200
    page2 = resp.json()
    assert {f["id"] for f in page2}.isdisjoint({f["id"] for f in first})


# --- Offset upper bound (FIX 2) --------------------------------------------


def test_list_offset_upper_bound_rejected(auth_client: httpx.Client):
    """Legacy offset paging is capped so a giant offset can't make the DB
    walk-and-discard an arbitrary number of rows. Over the cap is a 422;
    the cap itself is still accepted."""
    over = auth_client.get("/v1/fronts", params={"offset": 10_001})
    assert over.status_code == 422, over.text

    at_cap = auth_client.get("/v1/fronts", params={"offset": 10_000})
    assert at_cap.status_code == 200, at_cap.text


# --- Concurrent switch serialisation (FIX 1) -------------------------------


def _concurrent_posts(token: str, payload: dict, n: int) -> list[int]:
    """Fire `n` identical POST /v1/fronts concurrently, each on its own
    client sharing `token`. Returns the list of status codes."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    base_url = os.environ["SHEAF_TEST_URL"]

    def _one(_: int) -> int:
        with httpx.Client(base_url=base_url) as c:
            c.headers["Authorization"] = token
            return c.post("/v1/fronts", json=payload).status_code

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(_one, range(n)))


def test_concurrent_duplicate_switches_serialise(auth_client: httpx.Client):
    """Two concurrent non-replace switches with the same member set must
    not both land: the per-system advisory lock serialises the duplicate
    check, so exactly one wins (201) and the rest get 409. Without the
    lock both reads miss each other's uncommitted insert and both create
    an open front with the same set."""
    token = auth_client.headers["Authorization"]
    member_id = _create_member(auth_client, "RaceDup")
    payload = {"member_ids": [member_id], "replace_fronts": False}

    codes = _concurrent_posts(token, payload, 5)
    assert codes.count(201) == 1, codes
    assert codes.count(409) == 4, codes

    # Exactly one open front with that set exists.
    current = auth_client.get("/v1/fronts/current").json()
    matching = [f for f in current if f["member_ids"] == [member_id]]
    assert len(matching) == 1, current


def test_concurrent_replace_switches_leave_one_open(auth_client: httpx.Client):
    """Concurrent replace switches serialise to a single open front: each
    auto-ends the currently-open set before opening its own, so once they
    run one-at-a-time only the last-committed front is left open."""
    token = auth_client.headers["Authorization"]
    member_id = _create_member(auth_client, "RaceReplace")
    payload = {"member_ids": [member_id], "replace_fronts": True}

    codes = _concurrent_posts(token, payload, 5)
    assert all(c == 201 for c in codes), codes

    current = auth_client.get("/v1/fronts/current").json()
    assert len(current) == 1, current
