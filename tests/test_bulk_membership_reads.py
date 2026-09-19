"""Group and tag membership in one request instead of `1 + N + M`.

The flags exist so a client (the watch especially, over a Bluetooth link) can
build a member to groups-and-tags map without a request per group. What these
tests pin is not the convenience but the three things that are easy to get
wrong and silent when you do:

- Null and `[]` mean different things, and a client caching one as the other
  would draw an empty group that is not empty.
- The members-side expansions cross a scope boundary the members router does
  not enforce, so asking without the scope is refused loudly rather than
  served a thinner object that looks like an answer.
- The counts and ids agree with the roster the old per-group endpoint returns,
  including for archived members. A tile scoped to a group that quietly
  disagreed with the group screen would be a bug nobody could see.
"""

from __future__ import annotations

import uuid

import httpx
import pytest


def _member(c: httpx.Client, name: str) -> str:
    r = c.post("/v1/members", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _group(c: httpx.Client, name: str) -> str:
    r = c.post("/v1/groups", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _tag(c: httpx.Client, name: str) -> str:
    r = c.post("/v1/tags", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _set_group_members(c: httpx.Client, gid: str, ids: list[str]) -> None:
    r = c.put(f"/v1/groups/{gid}/members", json={"member_ids": ids})
    assert r.status_code == 200, r.text


def _set_tag_members(c: httpx.Client, tid: str, ids: list[str]) -> None:
    r = c.put(f"/v1/tags/{tid}/members", json={"member_ids": ids})
    assert r.status_code == 200, r.text


def test_group_member_ids_absent_unless_asked(auth_client: httpx.Client):
    """Null is "you did not ask". Existing callers' payloads must not grow."""
    gid = _group(auth_client, f"G-{uuid.uuid4().hex[:6]}")
    _set_group_members(auth_client, gid, [_member(auth_client, "Ada")])

    listed = auth_client.get("/v1/groups").json()
    mine = next(g for g in listed if g["id"] == gid)
    assert mine["member_ids"] is None

    detail = auth_client.get(f"/v1/groups/{gid}").json()
    assert detail["member_ids"] is None


def test_empty_group_asked_about_is_empty_list_not_null(
    auth_client: httpx.Client,
):
    """The distinction the whole contract rests on.

    A group with no members, when asked about, is `[]`. If it came back null a
    client could not tell it from "not requested" and would either re-fetch
    forever or render the group as unknown.
    """
    gid = _group(auth_client, f"Empty-{uuid.uuid4().hex[:6]}")

    listed = auth_client.get("/v1/groups?include_member_ids=true").json()
    mine = next(g for g in listed if g["id"] == gid)
    assert mine["member_ids"] == []
    assert mine["member_count"] == 0

    detail = auth_client.get(
        f"/v1/groups/{gid}?include_member_ids=true"
    ).json()
    assert detail["member_ids"] == []


def test_group_ids_match_the_per_group_endpoint(auth_client: httpx.Client):
    """The bulk answer and the one-request-per-group answer agree.

    This is the drift guard. The expansion exists to replace those calls, so
    the day it returns something different is the day a tile and the group
    screen start disagreeing.
    """
    gid = _group(auth_client, f"G-{uuid.uuid4().hex[:6]}")
    ids = [_member(auth_client, f"M{i}-{uuid.uuid4().hex[:4]}") for i in range(3)]
    _set_group_members(auth_client, gid, ids)

    bulk = auth_client.get("/v1/groups?include_member_ids=true").json()
    mine = next(g for g in bulk if g["id"] == gid)

    roster = auth_client.get(f"/v1/groups/{gid}/members").json()
    assert sorted(mine["member_ids"]) == sorted(m["id"] for m in roster)
    assert mine["member_count"] == len(roster) == 3


def test_archived_members_stay_in_the_membership(auth_client: httpx.Client):
    """Archiving hides a member from lists; it does not remove them from a
    group. The count and the ids must say so, or a group screen and a tile
    scoped to that group disagree."""
    gid = _group(auth_client, f"G-{uuid.uuid4().hex[:6]}")
    keep = _member(auth_client, f"Keep-{uuid.uuid4().hex[:4]}")
    gone = _member(auth_client, f"Archived-{uuid.uuid4().hex[:4]}")
    _set_group_members(auth_client, gid, [keep, gone])

    r = auth_client.post(f"/v1/members/{gone}/archive")
    assert r.status_code in (200, 204), r.text

    detail = auth_client.get(
        f"/v1/groups/{gid}?include_member_ids=true"
    ).json()
    assert sorted(detail["member_ids"]) == sorted([keep, gone])
    assert detail["member_count"] == 2


def test_member_count_is_always_present_and_right(auth_client: httpx.Client):
    """Unconditional, unlike the id list: it is cheap and it saves a list
    screen fetching a roster purely to count it. Checked on the write paths
    too, because those return the ORM row and would serialise the default."""
    gid = _group(auth_client, f"G-{uuid.uuid4().hex[:6]}")
    created = auth_client.get(f"/v1/groups/{gid}").json()
    assert created["member_count"] == 0

    _set_group_members(
        auth_client, gid, [_member(auth_client, f"A-{uuid.uuid4().hex[:4]}")]
    )

    patched = auth_client.patch(
        f"/v1/groups/{gid}", json={"name": f"Renamed-{uuid.uuid4().hex[:4]}"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["member_count"] == 1, (
        "a write response must not report the default count while the list "
        "endpoint reports the real one"
    )


def test_tag_member_ids(auth_client: httpx.Client):
    tid = _tag(auth_client, f"T-{uuid.uuid4().hex[:6]}")
    ids = [_member(auth_client, f"T{i}-{uuid.uuid4().hex[:4]}") for i in range(2)]
    _set_tag_members(auth_client, tid, ids)

    listed = auth_client.get("/v1/tags").json()
    assert next(t for t in listed if t["id"] == tid)["member_ids"] is None

    asked = auth_client.get("/v1/tags?include_member_ids=true").json()
    mine = next(t for t in asked if t["id"] == tid)
    assert sorted(mine["member_ids"]) == sorted(ids)

    detail = auth_client.get(f"/v1/tags/{tid}?include_member_ids=true").json()
    assert sorted(detail["member_ids"]) == sorted(ids)


def test_members_expansion_keyed_the_other_way(auth_client: httpx.Client):
    """Same rows, keyed from the member, for a client that starts there."""
    gid = _group(auth_client, f"G-{uuid.uuid4().hex[:6]}")
    tid = _tag(auth_client, f"T-{uuid.uuid4().hex[:6]}")
    mid = _member(auth_client, f"Both-{uuid.uuid4().hex[:4]}")
    _set_group_members(auth_client, gid, [mid])
    _set_tag_members(auth_client, tid, [mid])

    plain = auth_client.get("/v1/members").json()
    me = next(m for m in plain if m["id"] == mid)
    assert me["group_ids"] is None and me["tag_ids"] is None

    asked = auth_client.get(
        "/v1/members?include_group_ids=true&include_tag_ids=true"
    ).json()
    me = next(m for m in asked if m["id"] == mid)
    assert me["group_ids"] == [gid]
    assert me["tag_ids"] == [tid]

    # A member in nothing gets empty lists, not nulls.
    lonely = _member(auth_client, f"Lonely-{uuid.uuid4().hex[:4]}")
    asked = auth_client.get(
        "/v1/members?include_group_ids=true&include_tag_ids=true"
    ).json()
    them = next(m for m in asked if m["id"] == lonely)
    assert them["group_ids"] == [] and them["tag_ids"] == []


@pytest.mark.parametrize(
    ("param", "scope", "needed"),
    [
        ("include_group_ids", "members:read", "groups:read"),
        ("include_tag_ids", "members:read", "tags:read"),
    ],
)
def test_members_expansion_refuses_without_the_owning_scope(
    auth_client: httpx.Client, param: str, scope: str, needed: str
):
    """The reason option B on /v1/members was nearly rejected outright.

    A key scoped to members alone must not learn the group or tag structure
    through a member listing. It is refused with a 403 that names the scope
    and what asked for it, rather than served a silently thinner object: a
    missing field is indistinguishable from "in no groups", and a client would
    believe it.
    """
    made = auth_client.post(
        "/v1/auth/keys",
        json={"name": f"scoped-{uuid.uuid4().hex[:6]}", "scopes": [scope]},
    )
    assert made.status_code == 201, made.text
    key = made.json()["key"]

    base = str(auth_client.base_url)
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"}) as c:
        # The endpoint itself is reachable on members:read alone.
        assert c.get("/v1/members").status_code == 200
        # The expansion is not.
        r = c.get(f"/v1/members?{param}=true")
        assert r.status_code == 403, r.text
        detail = r.json()["detail"]
        assert needed in detail, detail
        assert param in detail, detail


def test_members_expansion_allowed_with_the_owning_scope(
    auth_client: httpx.Client,
):
    """And the same key with the scope added gets the data, so the refusal is
    about scope rather than about API keys."""
    made = auth_client.post(
        "/v1/auth/keys",
        json={
            "name": f"scoped-{uuid.uuid4().hex[:6]}",
            "scopes": ["members:read", "groups:read"],
        },
    )
    assert made.status_code == 201, made.text
    key = made.json()["key"]

    base = str(auth_client.base_url)
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"}) as c:
        r = c.get("/v1/members?include_group_ids=true")
        assert r.status_code == 200, r.text
        assert all(m["group_ids"] is not None for m in r.json())


def test_write_scope_implies_read_for_the_expansion(auth_client: httpx.Client):
    """`groups:write` satisfies `groups:read`, exactly as it does at the
    router. The check reuses `has_scope` so the two cannot drift; this is what
    would catch it if someone reimplemented it as a plain `in scopes`."""
    made = auth_client.post(
        "/v1/auth/keys",
        json={
            "name": f"scoped-{uuid.uuid4().hex[:6]}",
            "scopes": ["members:read", "groups:write"],
        },
    )
    assert made.status_code == 201, made.text
    key = made.json()["key"]

    base = str(auth_client.base_url)
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"}) as c:
        r = c.get("/v1/members?include_group_ids=true")
        assert r.status_code == 200, r.text
