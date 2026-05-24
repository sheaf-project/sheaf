"""Top-fronters quick-switch ranking.

Unit tests for the recency scorer (pure, no server) plus integration
tests for the endpoint's ordering, pinning, and limit behaviour.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx

from sheaf.services.analytics import FrontInterval, score_recent_fronters
from sheaf.services.sheaf_import import _coerce_pin

# --- scorer unit tests -----------------------------------------------------

_NOW = datetime(2026, 5, 1, tzinfo=UTC)


def _iv(start: datetime, end: datetime, members: list[uuid.UUID]) -> FrontInterval:
    return FrontInterval(start=start, end=end, member_ids=members)


def test_scorer_recent_beats_old_for_equal_duration():
    a, b = uuid.uuid4(), uuid.uuid4()
    hour = timedelta(hours=1)
    scores = score_recent_fronters(
        [
            _iv(_NOW - timedelta(days=1) - hour, _NOW - timedelta(days=1), [a]),
            _iv(_NOW - timedelta(days=60) - hour, _NOW - timedelta(days=60), [b]),
        ],
        now=_NOW,
        half_life_days=30.0,
    )
    assert scores[a] > scores[b]
    # 60 days is two half-lives, so b's weight is ~1/4 of a near-now front.
    assert scores[b] < scores[a] / 3


def test_scorer_cofront_counts_for_every_participant():
    a, b = uuid.uuid4(), uuid.uuid4()
    scores = score_recent_fronters(
        [_iv(_NOW - timedelta(hours=2), _NOW - timedelta(hours=1), [a, b])],
        now=_NOW,
    )
    assert scores[a] == scores[b] > 0


def test_scorer_skips_zero_duration_and_empty_membership():
    a = uuid.uuid4()
    scores = score_recent_fronters(
        [
            _iv(_NOW, _NOW, [a]),  # zero duration
            _iv(_NOW - timedelta(hours=1), _NOW, []),  # nobody fronting
        ],
        now=_NOW,
    )
    assert scores == {}


# --- endpoint integration --------------------------------------------------


def _member(client: httpx.Client, name: str, **extra) -> dict:
    r = client.post("/v1/members", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _ids(client: httpx.Client, **params) -> list[str]:
    r = client.get("/v1/members/top-fronters", params=params)
    assert r.status_code == 200, r.text
    return [m["id"] for m in r.json()]


def test_member_create_pin_defaults_null(auth_client: httpx.Client):
    m = _member(auth_client, f"Default-{uuid.uuid4().hex[:6]}")
    assert m["quick_switch_pin"] is None


def test_ranks_fronted_above_never_fronted(auth_client: httpx.Client):
    a = _member(auth_client, f"A-{uuid.uuid4().hex[:6]}")
    b = _member(auth_client, f"B-{uuid.uuid4().hex[:6]}")
    auth_client.post("/v1/fronts", json={"member_ids": [a["id"]]})

    ids = _ids(auth_client)
    assert ids.index(a["id"]) < ids.index(b["id"])


def test_pinned_member_comes_first_despite_no_fronting(auth_client: httpx.Client):
    a = _member(auth_client, f"Fronter-{uuid.uuid4().hex[:6]}")
    b = _member(auth_client, f"Pinned-{uuid.uuid4().hex[:6]}")
    auth_client.post("/v1/fronts", json={"member_ids": [a["id"]]})
    auth_client.patch(f"/v1/members/{b['id']}", json={"quick_switch_pin": 0})

    assert _ids(auth_client)[0] == b["id"]


def test_pins_order_by_priority_ascending(auth_client: httpx.Client):
    a = _member(auth_client, f"Five-{uuid.uuid4().hex[:6]}")
    b = _member(auth_client, f"One-{uuid.uuid4().hex[:6]}")
    auth_client.patch(f"/v1/members/{a['id']}", json={"quick_switch_pin": 5})
    auth_client.patch(f"/v1/members/{b['id']}", json={"quick_switch_pin": 1})

    ids = _ids(auth_client)
    assert ids[0] == b["id"]
    assert ids[1] == a["id"]


def test_limit_caps_result_count(auth_client: httpx.Client):
    for i in range(3):
        _member(auth_client, f"M{i}-{uuid.uuid4().hex[:6]}")
    assert len(_ids(auth_client, limit=2)) == 2


def test_coerce_pin_guards_junk():
    assert _coerce_pin(3) == 3
    assert _coerce_pin(0) == 0
    assert _coerce_pin(-1) is None
    assert _coerce_pin(None) is None
    assert _coerce_pin(True) is None  # bool is an int subclass; reject it
    assert _coerce_pin("5") is None


def test_export_includes_quick_switch_pin(auth_client: httpx.Client):
    m = _member(auth_client, f"Pinned-{uuid.uuid4().hex[:6]}")
    auth_client.patch(f"/v1/members/{m['id']}", json={"quick_switch_pin": 2})
    export = auth_client.get("/v1/export").json()
    by_id = {x["id"]: x for x in export["members"]}
    assert by_id[m["id"]]["quick_switch_pin"] == 2


def test_pin_set_and_clear_via_member_patch(auth_client: httpx.Client):
    m = _member(auth_client, f"Pinnable-{uuid.uuid4().hex[:6]}")
    set_resp = auth_client.patch(
        f"/v1/members/{m['id']}", json={"quick_switch_pin": 3}
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["quick_switch_pin"] == 3

    clear_resp = auth_client.patch(
        f"/v1/members/{m['id']}", json={"quick_switch_pin": None}
    )
    assert clear_resp.status_code == 200
    assert clear_resp.json()["quick_switch_pin"] is None
