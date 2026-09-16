"""Drift guard: the SQL fronting score must agree with the Python reference.

`/v1/members/top-fronters` scores in the database rather than loading every
front in its 180-day window, because that cost grows with the user's history
forever (see `score_recent_fronters_sql`). That means the scoring maths exists
twice: once readably in `score_recent_fronters`, once in SQL.

This file is what makes that safe. It builds one set of fronts, runs both
implementations over it, and fails if they disagree - so a change to either
that is not mirrored in the other is caught here rather than by a user noticing
their quick-switch list is ordered oddly.

If you are here because this failed: the Python version is the reference. Fix
whichever one is wrong, do not relax the comparison.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from sheaf.services.analytics import clip_intervals, score_recent_fronters

HALF_LIFE_DAYS = 30.0
WINDOW = timedelta(days=180)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _python_scores(
    rows: list[tuple[datetime, datetime | None, list[uuid.UUID]]],
    *,
    now: datetime,
) -> dict[uuid.UUID, float]:
    intervals = clip_intervals(rows, since=now - WINDOW, until=now)
    return score_recent_fronters(
        intervals, now=now, half_life_days=HALF_LIFE_DAYS
    )


def test_top_fronters_sql_matches_python_reference(auth_client: httpx.Client):
    """Both implementations, one set of fronts, same ranking.

    The fronts deliberately cover the cases the two could disagree on: one
    wholly inside the window, one straddling the far edge (clipped), one that
    ended before the window (excluded), one still open (ends at `now`), and a
    co-front (counts for both members).
    """
    now = datetime.now(UTC).replace(microsecond=0)
    since = now - WINDOW

    names = ["Ada", "Bo", "Cy"]
    members = {
        n: auth_client.post("/v1/members", json={"name": n}).json()["id"]
        for n in names
    }

    # (start, end or None, member names) - end None means still fronting.
    plan: list[tuple[datetime, datetime | None, list[str]]] = [
        # Recent and long: should dominate.
        (now - timedelta(days=2), now - timedelta(days=1), ["Ada"]),
        # Old but inside the window: decayed heavily.
        (now - timedelta(days=150), now - timedelta(days=149), ["Bo"]),
        # Straddles the window's far edge, so only the tail counts.
        (since - timedelta(days=5), since + timedelta(days=2), ["Cy"]),
        # Entirely before the window: must contribute nothing.
        (since - timedelta(days=40), since - timedelta(days=39), ["Cy"]),
        # Co-front: both accrue the same weight.
        (now - timedelta(days=5), now - timedelta(days=4), ["Ada", "Bo"]),
        # Open front: treated as ending now.
        (now - timedelta(hours=6), None, ["Cy"]),
    ]

    rows: list[tuple[datetime, datetime | None, list[uuid.UUID]]] = []
    for start, end, who in plan:
        front = auth_client.post(
            "/v1/fronts",
            json={
                "member_ids": [members[n] for n in who],
                "started_at": _iso(start),
            },
        ).json()
        if end is not None:
            r = auth_client.patch(
                f"/v1/fronts/{front['id']}", json={"ended_at": _iso(end)}
            )
            assert r.status_code == 200, r.text
        rows.append((start, end, [uuid.UUID(members[n]) for n in who]))

    expected = _python_scores(rows, now=now)

    # The endpoint exposes the SQL scoring only through its ordering, which is
    # the thing users actually see, so that is what is compared.
    got = auth_client.get("/v1/members/top-fronters?limit=50")
    assert got.status_code == 200, got.text
    returned = [m["id"] for m in got.json()]

    ranked = sorted(
        members.values(),
        key=lambda mid: (-expected.get(uuid.UUID(mid), 0.0), str(mid)),
    )
    assert returned[: len(ranked)] == ranked, (
        "SQL ordering diverged from the Python reference.\n"
        f"reference scores: {expected}\n"
        f"endpoint order:   {returned}"
    )


def test_top_fronters_excludes_fronts_entirely_before_the_window(
    auth_client: httpx.Client,
):
    """A member whose only fronting predates the window scores nothing.

    Pinned separately from the parity test because it is the case where an
    off-by-one in either implementation is silent: the member still appears in
    the list (everyone does, the list is padded), just at the bottom.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    old = auth_client.post("/v1/members", json={"name": "Ancient"}).json()
    recent = auth_client.post("/v1/members", json={"name": "Current"}).json()

    for member_id, start, end in (
        (old["id"], now - WINDOW - timedelta(days=10),
         now - WINDOW - timedelta(days=9)),
        (recent["id"], now - timedelta(days=1), now - timedelta(hours=12)),
    ):
        front = auth_client.post(
            "/v1/fronts",
            json={"member_ids": [member_id], "started_at": _iso(start)},
        ).json()
        auth_client.patch(
            f"/v1/fronts/{front['id']}", json={"ended_at": _iso(end)}
        )

    order = [m["id"] for m in auth_client.get("/v1/members/top-fronters?limit=50").json()]
    assert order.index(recent["id"]) < order.index(old["id"])


@pytest.mark.parametrize("limit", [1, 8, 50])
def test_top_fronters_respects_limit(auth_client: httpx.Client, limit: int):
    for i in range(3):
        auth_client.post("/v1/members", json={"name": f"P{i}"})
    r = auth_client.get(f"/v1/members/top-fronters?limit={limit}")
    assert r.status_code == 200, r.text
    assert len(r.json()) <= limit
