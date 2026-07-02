"""Integration tests for the fronting analytics endpoint."""

from datetime import UTC, datetime, timedelta

import httpx


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def test_analytics_default_window_returns_30_days(auth_client: httpx.Client):
    """No since/until → endpoint returns the last 30 days."""
    auth_client.post("/v1/members", json={"name": "Solo"})

    resp = auth_client.get("/v1/analytics/fronting")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["tz"] == "UTC"
    # 30 days = 2592000 seconds; allow a tiny slack since endpoint computes now()
    assert abs(data["window_seconds"] - 30 * 86400) < 5


def test_analytics_includes_zero_front_members(auth_client: httpx.Client):
    """Members with no fronting time still appear in the response, with
    zero stats. UI sorts by total_seconds desc; they fall to the bottom."""
    auth_client.post("/v1/members", json={"name": "Active"})
    auth_client.post("/v1/members", json={"name": "Inactive"})

    data = auth_client.get("/v1/analytics/fronting").json()
    names_present = len(data["members"])
    assert names_present == 2
    for stats in data["members"]:
        assert stats["total_seconds"] == 0
        assert stats["hour_of_day_seconds"] == [0] * 24


def test_analytics_credits_full_session_within_window(
    auth_client: httpx.Client,
):
    """A 2-hour front entirely inside the window should give that member
    7200 seconds of total_seconds."""
    member = auth_client.post("/v1/members", json={"name": "Working"}).json()
    start = (datetime.now(UTC) - timedelta(hours=4)).replace(microsecond=0)
    end = (datetime.now(UTC) - timedelta(hours=2)).replace(microsecond=0)
    front = auth_client.post(
        "/v1/fronts",
        json={"member_ids": [member["id"]], "started_at": _iso(start)},
    ).json()
    auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"ended_at": _iso(end)}
    )

    data = auth_client.get("/v1/analytics/fronting").json()
    by_id = {m["member_id"]: m for m in data["members"]}
    assert by_id[member["id"]]["total_seconds"] == 7200
    assert by_id[member["id"]]["session_count"] == 1


def test_analytics_double_counts_co_fronting(auth_client: httpx.Client):
    """Co-fronting should credit each member individually."""
    alice = auth_client.post("/v1/members", json={"name": "Alice"}).json()
    bob = auth_client.post("/v1/members", json={"name": "Bob"}).json()
    start = (datetime.now(UTC) - timedelta(hours=2)).replace(microsecond=0)
    end = (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0)
    front = auth_client.post(
        "/v1/fronts",
        json={
            "member_ids": [alice["id"], bob["id"]],
            "started_at": _iso(start),
        },
    ).json()
    auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"ended_at": _iso(end)}
    )

    data = auth_client.get("/v1/analytics/fronting").json()
    by_id = {m["member_id"]: m for m in data["members"]}
    assert by_id[alice["id"]]["total_seconds"] == 3600
    assert by_id[bob["id"]]["total_seconds"] == 3600


def test_analytics_marks_custom_fronts(auth_client: httpx.Client):
    """The is_custom_front flag rides along on the per-member stats so
    the UI can filter custom fronts out of headcount-style charts."""
    auth_client.post(
        "/v1/members", json={"name": "Asleep", "is_custom_front": True}
    )
    auth_client.post("/v1/members", json={"name": "RealMember"})

    data = auth_client.get("/v1/analytics/fronting").json()
    flags = {m["is_custom_front"] for m in data["members"]}
    assert flags == {True, False}


def test_analytics_rejects_inverted_window(auth_client: httpx.Client):
    """until before since is a 400."""
    until = datetime.now(UTC) - timedelta(days=10)
    since = datetime.now(UTC)
    resp = auth_client.get(
        "/v1/analytics/fronting",
        params={"since": _iso(since), "until": _iso(until)},
    )
    assert resp.status_code == 400


def test_analytics_rejects_invalid_timezone(auth_client: httpx.Client):
    resp = auth_client.get(
        "/v1/analytics/fronting", params={"tz": "Mars/Olympus_Mons"}
    )
    assert resp.status_code == 400


def test_analytics_rejects_window_over_5_years(auth_client: httpx.Client):
    """Hard cap on window length so a malformed request can't trigger an
    unbounded scan over a corrupted history."""
    until = datetime.now(UTC)
    since = until - timedelta(days=365 * 6)
    resp = auth_client.get(
        "/v1/analytics/fronting",
        params={"since": _iso(since), "until": _iso(until)},
    )
    assert resp.status_code == 400


def test_analytics_clips_session_at_window_edge(auth_client: httpx.Client):
    """A session that started before the window and continues inside
    should only count the inside-window portion."""
    member = auth_client.post("/v1/members", json={"name": "Rolling"}).json()
    # Session started 5h ago, ended 1h ago. Window is the last 3h.
    started = datetime.now(UTC) - timedelta(hours=5)
    ended = datetime.now(UTC) - timedelta(hours=1)
    front = auth_client.post(
        "/v1/fronts",
        json={"member_ids": [member["id"]], "started_at": _iso(started)},
    ).json()
    auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"ended_at": _iso(ended)}
    )

    since = datetime.now(UTC) - timedelta(hours=3)
    resp = auth_client.get(
        "/v1/analytics/fronting",
        params={"since": _iso(since)},
    )
    assert resp.status_code == 200
    by_id = {m["member_id"]: m for m in resp.json()["members"]}
    # Only 2 hours fall inside the window (since=now-3h, ended=now-1h).
    # Allow ~1s slack for rounding/clock skew between requests.
    assert abs(by_id[member["id"]]["total_seconds"] - 7200) <= 2


def test_analytics_treats_ongoing_front_as_ending_at_until(
    auth_client: httpx.Client,
):
    """Ongoing fronts (ended_at IS NULL) should be counted up to `until`."""
    member = auth_client.post("/v1/members", json={"name": "Open"}).json()
    started = datetime.now(UTC) - timedelta(hours=2)
    auth_client.post(
        "/v1/fronts",
        json={"member_ids": [member["id"]], "started_at": _iso(started)},
    )

    data = auth_client.get("/v1/analytics/fronting").json()
    by_id = {m["member_id"]: m for m in data["members"]}
    # ~2 hours, allow 5s slack since "now" moves between calls
    diff = abs(by_id[member["id"]]["total_seconds"] - 7200)
    assert diff <= 5, f"expected ~7200s, got {by_id[member['id']]['total_seconds']}"


def test_analytics_longest_session_and_counts(auth_client: httpx.Client):
    """total_seconds SUMs, session_count COUNTs and longest_session_seconds
    MAXes across a member's fronts - the aggregation the SQL path computes."""
    member = auth_client.post("/v1/members", json={"name": "Busy"}).json()
    now = datetime.now(UTC)
    sessions = [
        (now - timedelta(hours=6), now - timedelta(hours=5)),  # 1h
        (now - timedelta(hours=4), now - timedelta(hours=1)),  # 3h (longest)
        (
            now - timedelta(hours=12),
            now - timedelta(hours=12) + timedelta(minutes=30),
        ),  # 30m
    ]
    for start, end in sessions:
        start = start.replace(microsecond=0)
        end = end.replace(microsecond=0)
        front = auth_client.post(
            "/v1/fronts",
            json={"member_ids": [member["id"]], "started_at": _iso(start)},
        ).json()
        auth_client.patch(
            f"/v1/fronts/{front['id']}", json={"ended_at": _iso(end)}
        )

    data = auth_client.get("/v1/analytics/fronting").json()
    stats = {m["member_id"]: m for m in data["members"]}[member["id"]]
    assert stats["session_count"] == 3
    assert stats["longest_session_seconds"] == 3 * 3600
    assert stats["total_seconds"] == 3600 + 3 * 3600 + 1800


def test_analytics_hour_of_day_respects_timezone(auth_client: httpx.Client):
    """hour_of_day_seconds bucket the front in the requested timezone. Uses a
    fixed-offset zone (Etc/GMT-5 = UTC+5, no DST) so the expected bucket is
    deterministic and Python/Postgres agree on the offset."""
    member = auth_client.post("/v1/members", json={"name": "Clocked"}).json()
    # A clean one-hour front aligned to a UTC hour boundary, a few hours ago
    # so it lands well inside the default 30-day window.
    start = (datetime.now(UTC) - timedelta(hours=5)).replace(
        minute=0, second=0, microsecond=0
    )
    end = start + timedelta(hours=1)
    front = auth_client.post(
        "/v1/fronts",
        json={"member_ids": [member["id"]], "started_at": _iso(start)},
    ).json()
    auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"ended_at": _iso(end)}
    )

    utc_data = auth_client.get(
        "/v1/analytics/fronting", params={"tz": "UTC"}
    ).json()
    utc_buckets = {m["member_id"]: m for m in utc_data["members"]}[
        member["id"]
    ]["hour_of_day_seconds"]
    assert utc_buckets[start.hour] == 3600
    assert sum(utc_buckets) == 3600

    # Etc/GMT-5 is UTC+5 (the POSIX sign is inverted), so the same wall-clock
    # hour shifts five buckets forward.
    plus5_data = auth_client.get(
        "/v1/analytics/fronting", params={"tz": "Etc/GMT-5"}
    ).json()
    plus5_buckets = {m["member_id"]: m for m in plus5_data["members"]}[
        member["id"]
    ]["hour_of_day_seconds"]
    assert plus5_buckets[(start.hour + 5) % 24] == 3600
    assert sum(plus5_buckets) == 3600


def test_analytics_percent_of_window(auth_client: httpx.Client):
    """percent_of_window should match total_seconds / window_seconds * 100."""
    member = auth_client.post("/v1/members", json={"name": "Percent"}).json()
    started = datetime.now(UTC) - timedelta(hours=12)
    ended = datetime.now(UTC) - timedelta(hours=6)
    front = auth_client.post(
        "/v1/fronts",
        json={"member_ids": [member["id"]], "started_at": _iso(started)},
    ).json()
    auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"ended_at": _iso(ended)}
    )

    # Default 30-day window. 6 hours / 720 hours ~= 0.83%.
    data = auth_client.get("/v1/analytics/fronting").json()
    by_id = {m["member_id"]: m for m in data["members"]}
    assert abs(by_id[member["id"]]["percent_of_window"] - 0.83) < 0.05
