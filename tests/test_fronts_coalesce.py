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
