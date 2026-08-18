"""Coalesce contiguous fronting: per-member effective fronting-since.

When a member appears in a chain of back-to-back front entries (each
entry's `ended_at` exactly matches the next entry's `started_at`),
`/v1/fronts/current` returns each open front with `member_since[mid]`
set to the earliest started_at in that chain — not the literal entry's
own started_at.

The toggle lives on `system.coalesce_contiguous_fronts` (default True).
"""

from __future__ import annotations

import time

import httpx


def _create_member(client: httpx.Client, name: str) -> str:
    r = client.post("/v1/members", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _current(client: httpx.Client) -> list[dict]:
    r = client.get("/v1/fronts/current")
    assert r.status_code == 200
    return r.json()


def _set_coalesce(client: httpx.Client, on: bool) -> None:
    r = client.patch(
        "/v1/systems/me", json={"coalesce_contiguous_fronts": on}
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Single-front baseline: member_since == front.started_at
# ---------------------------------------------------------------------------


def test_single_front_member_since_equals_started_at(auth_client: httpx.Client):
    a = _create_member(auth_client, "Alice")
    auth_client.post("/v1/fronts", json={"member_ids": [a]})

    fronts = _current(auth_client)
    assert len(fronts) == 1
    front = fronts[0]
    assert front["member_since"][a] == front["started_at"]


# ---------------------------------------------------------------------------
# Chain across solo -> cofront (replace_fronts=True): coalesce kicks in
# ---------------------------------------------------------------------------


def test_solo_then_cofront_coalesces_for_persisting_member(
    auth_client: httpx.Client,
):
    a = _create_member(auth_client, "Alice")
    b = _create_member(auth_client, "Bob")

    # Front 1: Alice solo. Capture its started_at.
    f1 = auth_client.post(
        "/v1/fronts", json={"member_ids": [a], "replace_fronts": True}
    ).json()
    f1_started = f1["started_at"]

    # Brief sleep so the second front has a clearly later started_at —
    # otherwise we can't distinguish coalesced (= f1) from literal (= f2).
    time.sleep(0.05)

    # Front 2: replace_fronts ends f1 at the new started_at, then opens
    # {Alice, Bob}.
    auth_client.post(
        "/v1/fronts",
        json={"member_ids": [a, b], "replace_fronts": True},
    )

    fronts = _current(auth_client)
    assert len(fronts) == 1
    front = fronts[0]

    # Alice was in both, contiguously: her since walks back to f1.
    assert front["member_since"][a] == f1_started
    # Bob is new in this entry; his since is the entry's own started_at.
    assert front["member_since"][b] == front["started_at"]
    assert front["member_since"][b] != f1_started


def test_chain_extends_past_two_entries(auth_client: httpx.Client):
    a = _create_member(auth_client, "Alice")
    b = _create_member(auth_client, "Bob")
    c = _create_member(auth_client, "Cara")

    f1 = auth_client.post(
        "/v1/fronts", json={"member_ids": [a], "replace_fronts": True}
    ).json()
    f1_started = f1["started_at"]
    time.sleep(0.05)
    auth_client.post(
        "/v1/fronts",
        json={"member_ids": [a, b], "replace_fronts": True},
    )
    time.sleep(0.05)
    auth_client.post(
        "/v1/fronts",
        json={"member_ids": [a, b, c], "replace_fronts": True},
    )

    fronts = _current(auth_client)
    front = fronts[0]
    # Alice walked through all three -> chain start.
    assert front["member_since"][a] == f1_started
    # Bob entered at f2 -> his since is f2's started_at, NOT f1.
    assert front["member_since"][b] != f1_started
    assert front["member_since"][b] != front["started_at"]  # he's persisted
    # Cara just joined -> entry's started_at.
    assert front["member_since"][c] == front["started_at"]


# ---------------------------------------------------------------------------
# Chain breaks on gap (member leaves and rejoins)
# ---------------------------------------------------------------------------


def test_gap_breaks_chain(auth_client: httpx.Client):
    """Front 1: Alice. Front 1 ends explicitly (no immediate replacement).
    Then Front 2: Alice. The gap between f1.ended_at and f2.started_at
    means no contiguous chain — Alice's since is just f2.started_at."""
    a = _create_member(auth_client, "Alice")

    f1 = auth_client.post("/v1/fronts", json={"member_ids": [a]}).json()
    # End f1 explicitly via PATCH (creates a gap before f2).
    auth_client.patch(
        f"/v1/fronts/{f1['id']}", json={"ended_at": f1["started_at"]}
    )
    time.sleep(0.05)

    f2 = auth_client.post(
        "/v1/fronts", json={"member_ids": [a], "replace_fronts": True}
    ).json()

    fronts = _current(auth_client)
    front = fronts[0]
    # No chain — since is f2's own started_at.
    assert front["member_since"][a] == f2["started_at"]


# ---------------------------------------------------------------------------
# Toggle off: literal entry started_at every time
# ---------------------------------------------------------------------------


def test_toggle_off_disables_coalesce(auth_client: httpx.Client):
    a = _create_member(auth_client, "Alice")
    b = _create_member(auth_client, "Bob")

    auth_client.post(
        "/v1/fronts", json={"member_ids": [a], "replace_fronts": True}
    )
    time.sleep(0.05)
    auth_client.post(
        "/v1/fronts",
        json={"member_ids": [a, b], "replace_fronts": True},
    )

    # Toggle off — Alice's since should now equal the entry's started_at,
    # not the chain start.
    _set_coalesce(auth_client, False)

    fronts = _current(auth_client)
    front = fronts[0]
    assert front["member_since"][a] == front["started_at"]
    assert front["member_since"][b] == front["started_at"]


# ---------------------------------------------------------------------------
# History endpoint: literal entry times always
# ---------------------------------------------------------------------------


def test_history_endpoint_uses_literal_started_at(auth_client: httpx.Client):
    """The /v1/fronts list endpoint returns historical entries with
    member_since[mid] == front.started_at — no walk-back. Coalescing
    is a 'currently fronting' display thing, not a history rewrite."""
    a = _create_member(auth_client, "Alice")
    b = _create_member(auth_client, "Bob")

    auth_client.post(
        "/v1/fronts", json={"member_ids": [a], "replace_fronts": True}
    )
    time.sleep(0.05)
    auth_client.post(
        "/v1/fronts",
        json={"member_ids": [a, b], "replace_fronts": True},
    )

    history = auth_client.get("/v1/fronts").json()
    for f in history:
        for mid, since in f["member_since"].items():
            assert since == f["started_at"], (
                f"History endpoint should not coalesce; "
                f"front {f['id']} member {mid} got {since}, "
                f"expected {f['started_at']}"
            )


# ---------------------------------------------------------------------------
# Schema field round-trip
# ---------------------------------------------------------------------------


def test_system_read_includes_coalesce_field(auth_client: httpx.Client):
    sys = auth_client.get("/v1/systems/me").json()
    assert "coalesce_contiguous_fronts" in sys
    # Default is True for new systems.
    assert sys["coalesce_contiguous_fronts"] is True


def test_system_patch_round_trips_coalesce_field(auth_client: httpx.Client):
    auth_client.patch(
        "/v1/systems/me", json={"coalesce_contiguous_fronts": False}
    )
    sys = auth_client.get("/v1/systems/me").json()
    assert sys["coalesce_contiguous_fronts"] is False


# ---------------------------------------------------------------------------
# replace_fronts auto-ended timestamp alignment (regression)
# ---------------------------------------------------------------------------


def test_replace_fronts_auto_end_aligns_with_new_started_at(
    auth_client: httpx.Client,
):
    """Strict equality between an auto-ended front's `ended_at` and the
    new front's `started_at` is what coalesce_contiguous_fronts relies on
    to detect a chain. Earlier code used two separate `datetime.now()`
    calls, leaving a ms-scale gap that always broke the chain."""
    a = _create_member(auth_client, "Alice")

    f1_resp = auth_client.post(
        "/v1/fronts", json={"member_ids": [a], "replace_fronts": True}
    )
    assert f1_resp.status_code == 201
    f1_id = f1_resp.json()["id"]

    f2 = auth_client.post(
        "/v1/fronts", json={"member_ids": [a], "replace_fronts": True}
    ).json()

    # Find f1 in history with its (now-set) ended_at.
    history = auth_client.get("/v1/fronts").json()
    f1 = next(f for f in history if f["id"] == f1_id)
    assert f1["ended_at"] == f2["started_at"]


# ---------------------------------------------------------------------------
# 2026-08-13 incident regression: dense shared-boundary history must not
# blow up the coalesce query
# ---------------------------------------------------------------------------


def _iso(dt) -> str:
    return dt.isoformat()


def _make_closed_front(
    client: httpx.Client, member_ids: list[str], started, ended
) -> str:
    """Create a front and immediately backdate it to [started, ended]."""
    f = client.post("/v1/fronts", json={"member_ids": member_ids}).json()
    r = client.patch(
        f"/v1/fronts/{f['id']}",
        json={"started_at": _iso(started), "ended_at": _iso(ended)},
    )
    assert r.status_code == 200, r.text
    return f["id"]


def test_dense_shared_boundary_history_regression(auth_client: httpx.Client):
    """The 2026-08-13 prod incident shape, miniaturised.

    A member's history where EVERY level of the chain has several fronts
    sharing the exact same boundary timestamps (what a second-rounded
    PluralKit import produces). The old recursive walk-back enumerated
    every predecessor PATH - K predecessors per level to the power of L
    levels (here 4^12 = ~16.7M chain rows, spilling to query temp; the
    real incident spilled ~19 GB and filled the disk). The set-based
    query is one sorted pass over the member's fronts, so this must
    return instantly and still produce the true chain start.
    """
    from datetime import UTC, datetime, timedelta

    a = _create_member(auth_client, "Avalanche")
    base = datetime(2024, 1, 1, tzinfo=UTC)
    hour = timedelta(hours=1)
    levels, per_level = 12, 4

    # L levels x K duplicate fronts, all spanning [base+i*1h, base+(i+1)*1h].
    for i in range(levels):
        for _ in range(per_level):
            _make_closed_front(auth_client, [a], base + i * hour, base + (i + 1) * hour)

    # The open front picks up exactly where the lattice ends.
    cur = auth_client.post("/v1/fronts", json={"member_ids": [a]}).json()
    r = auth_client.patch(
        f"/v1/fronts/{cur['id']}", json={"started_at": _iso(base + levels * hour)}
    )
    assert r.status_code == 200, r.text

    t0 = time.monotonic()
    fronts = _current(auth_client)
    elapsed = time.monotonic() - t0

    assert len(fronts) == 1
    since = datetime.fromisoformat(fronts[0]["member_since"][a])
    assert since == base, f"expected chain start {base}, got {since}"
    # Generous bound: the query is a single sorted pass over ~49 rows. The
    # old path-enumerating query would spill for minutes on this shape.
    assert elapsed < 10, f"/current took {elapsed:.1f}s on the dense fixture"
    # No member may be flagged as depth-capped: the set-based query has no cap.
    assert fronts[0]["member_since_capped"] == []


def test_overlapping_fronts_merge_into_one_run(auth_client: httpx.Client):
    """Overlapping fronts for the same member merge into one contiguous run.

    Interval-merge contiguity (deliberate semantic refinement over the old
    exact-boundary walk): A [10:00-11:00] and B [10:30-11:30] overlap, and
    the open front starts at B's end. The member was continuously fronting
    from 10:00, so member_since walks through the overlap to A's start.
    The old recursive query would have stopped at B (10:30) because no
    front's ended_at == 10:30 exactly.
    """
    from datetime import UTC, datetime, timedelta

    a = _create_member(auth_client, "Overlap")
    base = datetime(2024, 6, 1, 10, 0, tzinfo=UTC)
    h = timedelta(hours=1)

    _make_closed_front(auth_client, [a], base, base + h)  # 10:00-11:00
    _make_closed_front(auth_client, [a], base + h / 2, base + h + h / 2)  # 10:30-11:30

    cur = auth_client.post("/v1/fronts", json={"member_ids": [a]}).json()
    r = auth_client.patch(
        f"/v1/fronts/{cur['id']}", json={"started_at": _iso(base + h + h / 2)}
    )
    assert r.status_code == 200, r.text

    fronts = _current(auth_client)
    assert len(fronts) == 1
    since = datetime.fromisoformat(fronts[0]["member_since"][a])
    assert since == base


def test_gap_still_breaks_run_with_dense_history(auth_client: httpx.Client):
    """A genuine gap still breaks the run even when the segments on either
    side are internally dense - the merge must not paper over real gaps."""
    from datetime import UTC, datetime, timedelta

    a = _create_member(auth_client, "Gappy")
    base = datetime(2024, 6, 2, 8, 0, tzinfo=UTC)
    h = timedelta(hours=1)

    # Dense island long before the gap: 8:00-9:00 twice, 9:00-10:00 twice.
    for _ in range(2):
        _make_closed_front(auth_client, [a], base, base + h)
        _make_closed_front(auth_client, [a], base + h, base + 2 * h)

    # Gap 10:00 -> 12:00, then the run containing the current front.
    _make_closed_front(auth_client, [a], base + 4 * h, base + 5 * h)  # 12:00-13:00
    cur = auth_client.post("/v1/fronts", json={"member_ids": [a]}).json()
    r = auth_client.patch(
        f"/v1/fronts/{cur['id']}", json={"started_at": _iso(base + 5 * h)}
    )
    assert r.status_code == 200, r.text

    fronts = _current(auth_client)
    since = datetime.fromisoformat(fronts[0]["member_since"][a])
    # Walks back through 13:00 <- 12:00 but NOT across the 10:00-12:00 gap.
    assert since == base + 4 * h


def test_multiple_open_fronts_report_one_continuous_since(
    auth_client: httpx.Client,
):
    """A member in two concurrently-open fronts is one continuously-fronting
    member: both entries report the same member_since (the earlier start).
    Under the old per-seed walk the later entry reported its own started_at
    even though the member never stopped fronting."""
    from datetime import UTC, datetime, timedelta

    a = _create_member(auth_client, "Ada")
    b = _create_member(auth_client, "Brook")
    base = datetime(2024, 6, 3, 9, 0, tzinfo=UTC)

    # replace_fronts=False explicitly: the system default (replace_fronts_default)
    # is True for new systems, which would auto-end f1 when f2 is created -
    # this test needs both fronts genuinely open at once.
    f1 = auth_client.post(
        "/v1/fronts", json={"member_ids": [a], "replace_fronts": False}
    ).json()
    r = auth_client.patch(f"/v1/fronts/{f1['id']}", json={"started_at": _iso(base)})
    assert r.status_code == 200, r.text

    f2 = auth_client.post(
        "/v1/fronts", json={"member_ids": [a, b], "replace_fronts": False}
    ).json()
    r = auth_client.patch(
        f"/v1/fronts/{f2['id']}",
        json={"started_at": _iso(base + timedelta(hours=1))},
    )
    assert r.status_code == 200, r.text

    fronts = _current(auth_client)
    assert len(fronts) == 2
    for f in fronts:
        since = datetime.fromisoformat(f["member_since"][a])
        assert since == base, (
            f"open front {f['id']}: Ada has been fronting continuously "
            f"since {base}, got {since}"
        )
