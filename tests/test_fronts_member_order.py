"""Member order within a front is deterministic.

The compact fronters view promises a stable order to clients that render the
list exactly as it arrives, and `/fronts/current` carries `member_ids` in the
same order. The association table has no position column, so without an
explicit ordering on the relationship the order is whatever the join returns,
which can differ between two identical requests. The relationship now orders
by member creation (id as tiebreak); this pins that the order is that one,
that it ignores the order the caller listed the ids in, and that it is the
same on every read and across both views.
"""

import httpx

from tests.test_fronts_compact import _compact, _create_member


def test_front_members_come_back_in_creation_order_every_time(auth_client: httpx.Client):
    first = _create_member(auth_client, "OrderFirst")
    second = _create_member(auth_client, "OrderSecond")
    third = _create_member(auth_client, "OrderThird")
    expected = [first, second, third]

    # Listed in a scrambled order on purpose: the caller's order is not the
    # contract, creation order is.
    resp = auth_client.post("/v1/fronts", json={"member_ids": [third, first, second]})
    assert resp.status_code == 201, resp.text

    for _ in range(3):
        current = auth_client.get("/v1/fronts/current")
        assert current.status_code == 200
        assert current.json()[0]["member_ids"] == expected

        compact = _compact(auth_client)
        assert [f["id"] for f in compact["fronters"]] == expected

    # The full history view walks the same relationship.
    listed = auth_client.get("/v1/fronts")
    assert listed.status_code == 200
    assert listed.json()[0]["member_ids"] == expected
