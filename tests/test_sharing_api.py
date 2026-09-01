"""HTTP-level tests for the share views + grants owner API.

Drive the running stack via `auth_client` (a freshly-registered, authenticated
user with its own system). These cover the things unit tests can't: tenant
isolation, the attestation gate, the one-public-grant rule, token handling, and
the asymmetric grace-window deferral end to end (including the finalize job).
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8001")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _member(c: httpx.Client, name: str, privacy: str = "private") -> str:
    r = c.post("/v1/members", json={"name": name, "privacy": privacy})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _group(c: httpx.Client, name: str) -> str:
    r = c.post("/v1/groups", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _field(c: httpx.Client, name: str) -> str:
    r = c.post("/v1/fields", json={"name": name, "field_type": "text"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _view(c: httpx.Client, name: str = "Public-ish", **kw) -> str:
    r = c.post("/v1/share-views", json={"name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _attest(c: httpx.Client) -> None:
    r = c.post("/v1/auth/me/attest-adult")
    assert r.status_code == 200, r.text


def _go_public(c: httpx.Client) -> None:
    """System privacy is the master ceiling over the public surface, so a system
    has to be public before it can publish anything at all."""
    r = c.patch("/v1/systems/me", json={"privacy": "public"})
    assert r.status_code == 200, r.text


def _second_client() -> httpx.Client:
    """A second registered+authed user (own system), for isolation tests."""
    c = httpx.Client(base_url=BASE_URL)
    email = f"share2-{uuid.uuid4().hex[:8]}@sheaf.dev"
    r = c.post("/v1/auth/register", json={"email": email, "password": "testpassword123"})
    assert r.status_code == 201
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


def _mark_never_shareable(c: httpx.Client, member_id: str) -> None:
    r = c.patch(f"/v1/members/{member_id}", json={"never_shareable": True})
    assert r.status_code == 200, r.text


def _arm_visibility_safety(c: httpx.Client) -> None:
    """Turn on the grace window for the profile_visibility category."""
    r = c.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_profile_visibility": True,
            "auth_tier": "password",
        },
    )
    assert r.status_code == 200, r.text


def _disarm_visibility_safety(c: httpx.Client) -> None:
    """Turn the profile_visibility category off. It defaults ON now, so a test
    that wants the pre-arm 'nothing is gated' baseline has to say so."""
    r = c.patch(
        "/v1/system/safety",
        json={"applies_to_profile_visibility": False},
    )
    assert r.status_code == 200, r.text


def _in_db(work) -> None:
    """Run `work(db)` straight against the test database, then commit.

    Only for the one thing the API deliberately cannot do on request: put an
    activation timestamp in the past so the finalize job has something due.
    """

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            await work(db)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _backdate_grant_activation(grant_id: str) -> None:
    """Push a pending grant's activation into the past so the finalize job fires."""

    async def _work(db) -> None:
        from sheaf.models.share import ShareGrant

        grant = await db.get(ShareGrant, uuid.UUID(grant_id))
        assert grant is not None
        grant.activates_at = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)


def _set_grant_activation_soon(grant_id: str) -> None:
    """Leave a pending grant with only a minute left in its own window."""

    async def _work(db) -> None:
        from sheaf.models.share import ShareGrant

        grant = await db.get(ShareGrant, uuid.UUID(grant_id))
        assert grant is not None
        grant.activates_at = datetime.now(UTC) + timedelta(minutes=1)

    _in_db(_work)


def _backdate_view_flags(view_id: str) -> None:
    """Same, for a view's staged flag flip."""

    async def _work(db) -> None:
        from sheaf.models.share import ShareView

        view = await db.get(ShareView, uuid.UUID(view_id))
        assert view is not None
        assert view.flags_activate_at is not None
        view.flags_activate_at = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)


def _backdate_fronting_guard(member_id: str) -> None:
    """Make a staged fronting-private release due for the finalize job."""

    async def _work(db) -> None:
        from sheaf.models.member import Member

        member = await db.get(Member, uuid.UUID(member_id))
        assert member is not None
        assert member.fronting_private_activates_at is not None
        member.fronting_private_activates_at = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)


# ---------------------------------------------------------------------------
# View CRUD
# ---------------------------------------------------------------------------


def test_view_create_list_get_delete(auth_client: httpx.Client):
    vid = _view(auth_client, "My view", include_bio=True)

    listed = auth_client.get("/v1/share-views").json()
    assert any(v["id"] == vid for v in listed)

    got = auth_client.get(f"/v1/share-views/{vid}").json()
    assert got["name"] == "My view"
    assert got["include_bio"] is True
    assert got["is_shared"] is False
    assert got["members"] == [] and got["fields"] == [] and got["groups"] == []

    assert auth_client.delete(f"/v1/share-views/{vid}").status_code == 204
    assert auth_client.get(f"/v1/share-views/{vid}").status_code == 404


def test_duplicate_view_name_conflicts(auth_client: httpx.Client):
    _view(auth_client, "Dupe")
    r = auth_client.post("/v1/share-views", json={"name": "Dupe"})
    assert r.status_code == 409


def test_add_and_remove_view_member(auth_client: httpx.Client):
    vid = _view(auth_client)
    m = _member(auth_client, "InView")

    r = auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
    assert r.status_code == 200, r.text
    assert any(vm["member_id"] == m for vm in r.json()["members"])
    # Unshared view, so the row is active immediately.
    assert r.json()["members"][0]["status"] == "active"

    assert (
        auth_client.delete(f"/v1/share-views/{vid}/members/{m}").status_code == 204
    )
    assert auth_client.get(f"/v1/share-views/{vid}").json()["members"] == []


def test_add_field_to_view(auth_client: httpx.Client):
    vid = _view(auth_client)
    f = _field(auth_client, "Favourite colour")
    r = auth_client.post(f"/v1/share-views/{vid}/fields", json={"field_id": f})
    assert r.status_code == 200, r.text
    assert any(vf["field_id"] == f for vf in r.json()["fields"])


# ---------------------------------------------------------------------------
# Hard guards
# ---------------------------------------------------------------------------


def test_never_shareable_member_rejected_at_add(auth_client: httpx.Client):
    vid = _view(auth_client)
    m = _member(auth_client, "Secret")
    _mark_never_shareable(auth_client, m)

    r = auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
    assert r.status_code == 400
    assert "never" in r.text.lower()


def test_marking_never_shareable_pulls_member_from_views(auth_client: httpx.Client):
    vid = _view(auth_client)
    m = _member(auth_client, "WasIn")
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
    assert len(auth_client.get(f"/v1/share-views/{vid}").json()["members"]) == 1

    _mark_never_shareable(auth_client, m)

    # Enforced, not just remembered: the row is gone.
    assert auth_client.get(f"/v1/share-views/{vid}").json()["members"] == []


# ---------------------------------------------------------------------------
# Group expansion (one-time pick, not a live rule)
# ---------------------------------------------------------------------------


def test_group_expands_current_members_and_skips_never_shareable(
    auth_client: httpx.Client,
):
    vid = _view(auth_client)
    # Public members get pulled in; a never-shareable and a non-public member
    # are both skipped by the bulk expansion, with separate counts.
    a = _member(auth_client, "GrpA", privacy="public")
    b = _member(auth_client, "GrpB", privacy="public")
    secret = _member(auth_client, "GrpSecret", privacy="public")
    _mark_never_shareable(auth_client, secret)
    priv = _member(auth_client, "GrpPrivate", privacy="private")
    g = _group(auth_client, "TheGroup")
    r = auth_client.put(
        f"/v1/groups/{g}/members", json={"member_ids": [a, b, secret, priv]}
    )
    assert r.status_code == 200, r.text

    res = auth_client.post(f"/v1/share-views/{vid}/groups", json={"group_id": g})
    assert res.status_code == 200, res.text
    assert res.json() == {
        "added": 2,
        "skipped_never_shareable": 1,
        "skipped_not_public": 1,
    }

    got = auth_client.get(f"/v1/share-views/{vid}").json()
    member_ids = {vm["member_id"] for vm in got["members"]}
    assert member_ids == {a, b}


def test_group_is_not_a_live_rule(auth_client: httpx.Client):
    """Adding to the group AFTER expansion does not pull the new member in."""
    vid = _view(auth_client)
    a = _member(auth_client, "Live1", privacy="public")
    g = _group(auth_client, "LiveGroup")
    auth_client.put(f"/v1/groups/{g}/members", json={"member_ids": [a]})
    auth_client.post(f"/v1/share-views/{vid}/groups", json={"group_id": g})

    late = _member(auth_client, "LateJoiner", privacy="public")
    auth_client.put(f"/v1/groups/{g}/members", json={"member_ids": [a, late]})

    got = auth_client.get(f"/v1/share-views/{vid}").json()
    member_ids = {vm["member_id"] for vm in got["members"]}
    assert member_ids == {a}  # late joiner NOT auto-added


# ---------------------------------------------------------------------------
# Group-expansion provenance (which rows a detach may remove)
# ---------------------------------------------------------------------------


def _sources(c: httpx.Client, view_id: str) -> dict[str, str | None]:
    """member_id -> the group expansion that created its row (None = manual)."""
    got = c.get(f"/v1/share-views/{view_id}").json()
    return {vm["member_id"]: vm["added_via_group_id"] for vm in got["members"]}


def test_expansion_stamps_its_rows_and_a_manual_add_is_unstamped(
    auth_client: httpx.Client,
):
    vid = _view(auth_client)
    from_group = _member(auth_client, "SrcGrouped", privacy="public")
    by_hand = _member(auth_client, "SrcManual", privacy="public")
    g = _group(auth_client, "SrcGroup")
    auth_client.put(f"/v1/groups/{g}/members", json={"member_ids": [from_group]})

    auth_client.post(f"/v1/share-views/{vid}/groups", json={"group_id": g})
    r = auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": by_hand})
    assert r.status_code == 200, r.text

    assert _sources(auth_client, vid) == {from_group: g, by_hand: None}


def test_expansion_leaves_an_existing_row_alone(auth_client: httpx.Client):
    """A member already in the view keeps their original source, whichever way
    round it happened: hand-picked stays hand-picked, and the first group to
    bring someone in stays the reason they are here."""
    vid = _view(auth_client)
    m = _member(auth_client, "AlreadyHere", privacy="public")
    first = _group(auth_client, "FirstGroup")
    second = _group(auth_client, "SecondGroup")
    auth_client.put(f"/v1/groups/{first}/members", json={"member_ids": [m]})
    auth_client.put(f"/v1/groups/{second}/members", json={"member_ids": [m]})

    auth_client.post(f"/v1/share-views/{vid}/groups", json={"group_id": first})
    res = auth_client.post(f"/v1/share-views/{vid}/groups", json={"group_id": second})
    # Nothing new to add: the member was already there.
    assert res.json()["added"] == 0
    assert _sources(auth_client, vid) == {m: first}

    hand = _member(auth_client, "HandFirst", privacy="public")
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": hand})
    third = _group(auth_client, "ThirdGroup")
    auth_client.put(f"/v1/groups/{third}/members", json={"member_ids": [hand]})
    auth_client.post(f"/v1/share-views/{vid}/groups", json={"group_id": third})
    assert _sources(auth_client, vid)[hand] is None


def test_detaching_a_group_removes_only_the_rows_it_added(
    auth_client: httpx.Client,
):
    """The finding this column exists for: a detach used to re-read the group's
    roster and pull out everybody in it, including people it never added."""
    vid = _view(auth_client)
    only_group = _member(auth_client, "OnlyGroup", privacy="public")
    also_manual = _member(auth_client, "AlsoManual", privacy="public")
    overlapping = _member(auth_client, "Overlapping", privacy="public")

    doomed = _group(auth_client, "Doomed")
    other = _group(auth_client, "Other")
    auth_client.put(
        f"/v1/groups/{doomed}/members",
        json={"member_ids": [only_group, also_manual, overlapping]},
    )
    auth_client.put(f"/v1/groups/{other}/members", json={"member_ids": [overlapping]})

    # `also_manual` is picked by hand FIRST, so their row is unstamped even
    # though the doomed group also contains them. `overlapping` arrives via the
    # other group first, so their row belongs to that one.
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": also_manual})
    auth_client.post(f"/v1/share-views/{vid}/groups", json={"group_id": other})
    auth_client.post(f"/v1/share-views/{vid}/groups", json={"group_id": doomed})
    assert set(_sources(auth_client, vid)) == {only_group, also_manual, overlapping}

    r = auth_client.delete(f"/v1/share-views/{vid}/groups/{doomed}")
    assert r.status_code == 204, r.text

    # Only the member this group actually added is gone.
    assert _sources(auth_client, vid) == {also_manual: None, overlapping: other}


def test_detach_still_removes_someone_who_has_left_the_group(
    auth_client: httpx.Client,
):
    """Reads backwards until you look at the row: leaving the group never moved
    them out of the view (group membership does not drive exposure), so this
    group IS still the reason they are in it, and detaching it takes them."""
    vid = _view(auth_client)
    leaver = _member(auth_client, "Leaver", privacy="public")
    stayer = _member(auth_client, "Stayer", privacy="public")
    g = _group(auth_client, "LeftGroup")
    auth_client.put(f"/v1/groups/{g}/members", json={"member_ids": [leaver, stayer]})
    auth_client.post(f"/v1/share-views/{vid}/groups", json={"group_id": g})

    # Out of the group, still in the view - that is the snapshot semantic.
    auth_client.put(f"/v1/groups/{g}/members", json={"member_ids": [stayer]})
    assert set(_sources(auth_client, vid)) == {leaver, stayer}

    auth_client.delete(f"/v1/share-views/{vid}/groups/{g}")
    assert _sources(auth_client, vid) == {}


def test_detach_keeps_members_when_asked(auth_client: httpx.Client):
    vid = _view(auth_client)
    m = _member(auth_client, "Kept", privacy="public")
    g = _group(auth_client, "KeepGroup")
    auth_client.put(f"/v1/groups/{g}/members", json={"member_ids": [m]})
    auth_client.post(f"/v1/share-views/{vid}/groups", json={"group_id": g})

    r = auth_client.delete(
        f"/v1/share-views/{vid}/groups/{g}", params={"remove_members": "false"}
    )
    assert r.status_code == 204, r.text
    # Still in the view, and still stamped - the stamp is attribution, not a
    # live rule, and the association row is what went away.
    assert _sources(auth_client, vid) == {m: g}
    assert auth_client.get(f"/v1/share-views/{vid}").json()["groups"] == []


def test_deleting_a_group_leaves_the_view_alone_and_clears_the_stamp(
    auth_client: httpx.Client,
):
    """The FK is ON DELETE SET NULL, deliberately: deleting a group must never
    silently un-publish the people it once added. They degrade to manual."""
    vid = _view(auth_client)
    m = _member(auth_client, "Orphaned", privacy="public")
    g = _group(auth_client, "DeletedGroup")
    auth_client.put(f"/v1/groups/{g}/members", json={"member_ids": [m]})
    auth_client.post(f"/v1/share-views/{vid}/groups", json={"group_id": g})
    assert _sources(auth_client, vid) == {m: g}

    r = auth_client.delete(f"/v1/groups/{g}")
    assert r.status_code in (200, 204), r.text

    # Member still in the view, now unattributed; the association row went with
    # the group, so there is nothing left to detach and nothing gets removed.
    assert _sources(auth_client, vid) == {m: None}
    got = auth_client.get(f"/v1/share-views/{vid}").json()
    assert got["groups"] == []
    assert auth_client.delete(f"/v1/share-views/{vid}/groups/{g}").status_code == 204
    assert _sources(auth_client, vid) == {m: None}


# ---------------------------------------------------------------------------
# Attestation gate
# ---------------------------------------------------------------------------


def test_grant_requires_adult_attestation(auth_client: httpx.Client):
    # Public system, so the refusal below can only be about the attestation.
    _go_public(auth_client)
    vid = _view(auth_client)
    r = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    assert r.status_code == 403
    assert "18" in r.text

    _attest(auth_client)
    r = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    assert r.status_code == 201, r.text


def test_attest_is_idempotent(auth_client: httpx.Client):
    first = auth_client.post("/v1/auth/me/attest-adult").json()["adult_attested_at"]
    second = auth_client.post("/v1/auth/me/attest-adult").json()["adult_attested_at"]
    assert first is not None and first == second
    # And it surfaces on /me.
    assert auth_client.get("/v1/auth/me").json()["adult_attested_at"] == first


# ---------------------------------------------------------------------------
# Grants: public uniqueness, link tokens
# ---------------------------------------------------------------------------


def test_only_one_public_grant_per_system(auth_client: httpx.Client):
    _go_public(auth_client)
    _attest(auth_client)
    v1 = _view(auth_client, "V1")
    v2 = _view(auth_client, "V2")
    first = auth_client.post(
        "/v1/share-grants", json={"view_id": v1, "subject_type": "public"}
    )
    assert first.status_code == 201, first.text
    r = auth_client.post(
        "/v1/share-grants", json={"view_id": v2, "subject_type": "public"}
    )
    assert r.status_code == 409

    # Revoking the first frees the slot.
    gid = next(
        g["id"]
        for g in auth_client.get("/v1/share-grants").json()
        if g["subject_type"] == "public"
    )
    assert auth_client.delete(f"/v1/share-grants/{gid}").status_code == 204
    freed = auth_client.post(
        "/v1/share-grants", json={"view_id": v2, "subject_type": "public"}
    )
    assert freed.status_code == 201, freed.text


def test_link_token_returned_once_and_rotates(auth_client: httpx.Client):
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client)
    created = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "link"}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    token = body["token"]
    assert token  # link grants return a raw token
    gid = body["grant"]["id"]

    # The token never comes back on any subsequent read.
    listed = auth_client.get("/v1/share-grants").json()
    assert all("token" not in g and "token_hash" not in g for g in listed)

    rotated = auth_client.post(f"/v1/share-grants/{gid}/rotate")
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["token"] and rotated.json()["token"] != token


def test_account_data_bundle_carries_grants_but_no_token(
    auth_client: httpx.Client,
):
    """Article 15 has to describe the exposure the account created, and must
    not leak any part of the link token doing it."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "Bundled", include_bio=True)
    token = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "link"}
    ).json()["token"]

    bundle = auth_client.post(
        "/v1/account/data", json={"password": "testpassword123"}
    )
    assert bundle.status_code == 200, bundle.text
    body = bundle.json()

    assert body["account"]["adult_attested_at"] is not None
    grant = next(g for g in body["share_grants"] if g["view_id"] == vid)
    assert grant["subject_type"] == "link"
    assert grant["view_name"] == "Bundled"
    assert grant["view_include_bio"] is True
    assert grant["status"] and grant["created_at"]

    serialised = str(body)
    assert token not in serialised
    assert "token_hash" not in serialised


def test_public_grant_carries_no_token(auth_client: httpx.Client):
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client)
    created = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    ).json()
    assert created["token"] is None


# ---------------------------------------------------------------------------
# Asymmetric deferral through the grace window
# ---------------------------------------------------------------------------


def test_grant_deferred_and_requires_reauth_when_safety_armed(
    auth_client: httpx.Client,
):
    _go_public(auth_client)
    _attest(auth_client)
    _arm_visibility_safety(auth_client)
    vid = _view(auth_client)

    # No credential -> the deferred exposure is refused.
    denied = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    assert denied.status_code in (400, 403), denied.text

    # With re-auth it is accepted, but lands pending (not yet live).
    ok = auth_client.post(
        "/v1/share-grants",
        json={"view_id": vid, "subject_type": "public", "password": "testpassword123"},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["grant"]["status"] == "pending"


def test_revoke_is_immediate_even_with_safety_armed(auth_client: httpx.Client):
    """Going dark never waits on the grace window."""
    _go_public(auth_client)
    _attest(auth_client)
    _arm_visibility_safety(auth_client)
    vid = _view(auth_client)
    gid = auth_client.post(
        "/v1/share-grants",
        json={"view_id": vid, "subject_type": "public", "password": "testpassword123"},
    ).json()["grant"]["id"]

    # No password, no waiting.
    assert auth_client.delete(f"/v1/share-grants/{gid}").status_code == 204
    assert all(
        g["status"] == "revoked"
        for g in auth_client.get("/v1/share-grants").json()
        if g["id"] == gid
    )


def test_finalize_job_promotes_pending_grant(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    _go_public(auth_client)
    _attest(auth_client)
    _arm_visibility_safety(auth_client)
    vid = _view(auth_client)
    gid = auth_client.post(
        "/v1/share-grants",
        json={"view_id": vid, "subject_type": "public", "password": "testpassword123"},
    ).json()["grant"]["id"]

    _backdate_grant_activation(gid)
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "success"

    now_active = next(
        g for g in auth_client.get("/v1/share-grants").json() if g["id"] == gid
    )
    assert now_active["status"] == "active"


def _shared_view(c: httpx.Client, **kw) -> str:
    """A view with a live public grant, created before safety is armed."""
    _go_public(c)
    _attest(c)
    vid = _view(c, f"Shared-{uuid.uuid4().hex[:6]}", **kw)
    r = c.post("/v1/share-grants", json={"view_id": vid, "subject_type": "public"})
    assert r.status_code == 201, r.text
    return vid


# ---------------------------------------------------------------------------
# View flag flips (staged like any other exposure)
# ---------------------------------------------------------------------------


def test_flag_loosening_on_a_shared_view_is_deferred(auth_client: httpx.Client):
    vid = _shared_view(auth_client)
    _arm_visibility_safety(auth_client)

    # No credential -> refused, exactly like adding a member would be.
    denied = auth_client.patch(f"/v1/share-views/{vid}", json={"include_bio": True})
    assert denied.status_code in (400, 403), denied.text

    ok = auth_client.patch(
        f"/v1/share-views/{vid}",
        json={"include_bio": True, "password": "testpassword123"},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    # Accepted, but staged: the live flag has not moved.
    assert body["include_bio"] is False
    assert body["pending_include_bio"] is True
    assert body["flags_activate_at"] is not None
    # Untouched flags stage nothing.
    assert body["pending_include_fronting"] is None


def test_turning_relationships_on_for_a_shared_view_is_deferred(
    auth_client: httpx.Client,
):
    """include_relationships is an exposure flag like the other three: turning
    it on can only ever add to what the view serves, so it is staged."""
    vid = _shared_view(auth_client)
    _arm_visibility_safety(auth_client)

    denied = auth_client.patch(
        f"/v1/share-views/{vid}", json={"include_relationships": True}
    )
    assert denied.status_code in (400, 403), denied.text

    ok = auth_client.patch(
        f"/v1/share-views/{vid}",
        json={"include_relationships": True, "password": "testpassword123"},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["include_relationships"] is False
    assert body["pending_include_relationships"] is True
    assert body["flags_activate_at"] is not None


def test_turning_groups_on_for_a_shared_view_is_deferred(
    auth_client: httpx.Client,
):
    """include_groups is an exposure flag: the endpoint it opens did not exist
    for visitors before, so turning it on can only add to what is served."""
    vid = _shared_view(auth_client)
    _arm_visibility_safety(auth_client)

    denied = auth_client.patch(
        f"/v1/share-views/{vid}", json={"include_groups": True}
    )
    assert denied.status_code in (400, 403), denied.text

    ok = auth_client.patch(
        f"/v1/share-views/{vid}",
        json={"include_groups": True, "password": "testpassword123"},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["include_groups"] is False
    assert body["pending_include_groups"] is True
    assert body["flags_activate_at"] is not None


def test_turning_the_member_list_back_on_is_deferred(auth_client: httpx.Client):
    """include_members is an exposure flag in the ON direction only.

    Turning it off is going dark and lands at once (see the tightening test);
    turning it back on republishes the whole roster, which is the largest
    single loosening a view has, so it waits like everything else."""
    vid = _shared_view(auth_client, include_members=False)
    _arm_visibility_safety(auth_client)

    denied = auth_client.patch(
        f"/v1/share-views/{vid}", json={"include_members": True}
    )
    assert denied.status_code in (400, 403), denied.text

    ok = auth_client.patch(
        f"/v1/share-views/{vid}",
        json={"include_members": True, "password": "testpassword123"},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["include_members"] is False
    assert body["pending_include_members"] is True
    assert body["flags_activate_at"] is not None


def test_turning_the_member_list_off_is_immediate(auth_client: httpx.Client):
    vid = _shared_view(auth_client)
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(
        f"/v1/share-views/{vid}", json={"include_members": False}
    )
    assert r.status_code == 200, r.text
    assert r.json()["include_members"] is False
    assert r.json()["pending_include_members"] is None
    assert r.json()["flags_activate_at"] is None


def test_member_permalinks_never_defers(auth_client: httpx.Client):
    """Permalinks are deliberately not an exposure flag.

    They publish nothing the roster does not already publish - they only give
    already-shown members a stable address - so turning them on while the view
    is shared and the safety category is armed still takes effect at once, with
    no step-up and nothing staged. There is no `pending_member_permalinks` to
    check because the column does not exist.
    """
    vid = _shared_view(auth_client)
    _arm_visibility_safety(auth_client)

    on = auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_permalinks": True}
    )
    assert on.status_code == 200, on.text
    assert on.json()["member_permalinks"] is True
    assert on.json()["flags_activate_at"] is None
    assert "pending_member_permalinks" not in on.json()

    off = auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_permalinks": False}
    )
    assert off.status_code == 200, off.text
    assert off.json()["member_permalinks"] is False


def test_permalinks_alongside_a_staged_flag_still_lands_at_once(
    auth_client: httpx.Client,
):
    """One PATCH carrying both kinds of change: the exposure flag stages, the
    permalink toggle does not get dragged into the grace window with it."""
    vid = _shared_view(auth_client)
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(
        f"/v1/share-views/{vid}",
        json={
            "include_bio": True,
            "member_permalinks": True,
            "password": "testpassword123",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["include_bio"] is False
    assert body["pending_include_bio"] is True
    assert body["member_permalinks"] is True


def test_flag_tightening_is_immediate_and_ungated(auth_client: httpx.Client):
    vid = _shared_view(auth_client, include_fronting=True)
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(
        f"/v1/share-views/{vid}", json={"include_fronting": False}
    )
    assert r.status_code == 200, r.text
    assert r.json()["include_fronting"] is False
    assert r.json()["pending_include_fronting"] is None


def test_turning_a_flag_off_cancels_its_pending_flip(auth_client: httpx.Client):
    """Going dark wins: a staged flip is dropped, not merely overridden."""
    vid = _shared_view(auth_client)
    _arm_visibility_safety(auth_client)
    staged = auth_client.patch(
        f"/v1/share-views/{vid}",
        json={"include_bio": True, "password": "testpassword123"},
    )
    assert staged.json()["pending_include_bio"] is True

    off = auth_client.patch(f"/v1/share-views/{vid}", json={"include_bio": False})
    assert off.status_code == 200, off.text
    assert off.json()["include_bio"] is False
    assert off.json()["pending_include_bio"] is None
    # Nothing staged anywhere -> no activation time left hanging around.
    assert off.json()["flags_activate_at"] is None


def test_flag_change_on_an_unshared_view_is_immediate(auth_client: httpx.Client):
    """Nothing points at the view, so turning a flag on exposes nothing."""
    _arm_visibility_safety(auth_client)
    vid = _view(auth_client)

    r = auth_client.patch(f"/v1/share-views/{vid}", json={"include_bio": True})
    assert r.status_code == 200, r.text
    assert r.json()["include_bio"] is True
    assert r.json()["pending_include_bio"] is None


def test_flag_change_without_the_category_armed_is_immediate(
    auth_client: httpx.Client,
):
    # Category on by default; disarm it to test the ungated flag-flip path.
    vid = _shared_view(auth_client)
    _disarm_visibility_safety(auth_client)
    r = auth_client.patch(f"/v1/share-views/{vid}", json={"include_bio": True})
    assert r.status_code == 200, r.text
    assert r.json()["include_bio"] is True
    assert r.json()["flags_activate_at"] is None


def test_finalize_job_promotes_a_staged_flag_flip(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    vid = _shared_view(auth_client)
    _arm_visibility_safety(auth_client)
    auth_client.patch(
        f"/v1/share-views/{vid}",
        json={
            "include_bio": True,
            "fronting_show_count": True,
            "password": "testpassword123",
        },
    )

    _backdate_view_flags(vid)
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text

    got = auth_client.get(f"/v1/share-views/{vid}").json()
    assert got["include_bio"] is True
    assert got["pending_include_bio"] is None
    assert got["flags_activate_at"] is None


def test_finalize_job_promotes_the_two_display_flags(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    """The sweep's conditional UPDATE has to name every staged flag; a new one
    left out of it would stage forever and never arrive."""
    vid = _shared_view(auth_client, include_members=False)
    _arm_visibility_safety(auth_client)
    staged = auth_client.patch(
        f"/v1/share-views/{vid}",
        json={
            "include_members": True,
            "include_groups": True,
            "password": "testpassword123",
        },
    )
    assert staged.status_code == 200, staged.text

    _backdate_view_flags(vid)
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text

    got = auth_client.get(f"/v1/share-views/{vid}").json()
    assert got["include_members"] is True
    assert got["include_groups"] is True
    assert got["pending_include_members"] is None
    assert got["pending_include_groups"] is None
    assert got["flags_activate_at"] is None


# ---------------------------------------------------------------------------
# Member-level visibility loosenings
# ---------------------------------------------------------------------------


def test_fronting_guard_release_is_reauthed_and_deferred(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    vid = _shared_view(auth_client, include_fronting=True)
    member = _member(auth_client, "GuardedFronter", privacy="public")
    auth_client.post(
        f"/v1/share-views/{vid}/members", json={"member_id": member}
    )
    auth_client.patch(
        f"/v1/members/{member}", json={"fronting_private": True}
    )
    _arm_visibility_safety(auth_client)

    denied = auth_client.patch(
        f"/v1/members/{member}", json={"fronting_private": False}
    )
    assert denied.status_code in (400, 403), denied.text

    staged = auth_client.patch(
        f"/v1/members/{member}",
        json={"fronting_private": False, "password": "testpassword123"},
    )
    assert staged.status_code == 200, staged.text
    assert staged.json()["fronting_private"] is True
    assert staged.json()["fronting_private_activates_at"] is not None

    _backdate_fronting_guard(member)
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text
    finalized = auth_client.get(f"/v1/members/{member}").json()
    assert finalized["fronting_private"] is False
    assert finalized["fronting_private_activates_at"] is None


def test_fronting_guard_release_without_exposure_is_immediate(
    auth_client: httpx.Client,
):
    member = _member(auth_client, "UnsharedFronter", privacy="public")
    guarded = auth_client.patch(
        f"/v1/members/{member}", json={"fronting_private": True}
    )
    assert guarded.status_code == 200, guarded.text
    _arm_visibility_safety(auth_client)

    released = auth_client.patch(
        f"/v1/members/{member}", json={"fronting_private": False}
    )
    assert released.status_code == 200, released.text
    assert released.json()["fronting_private"] is False
    assert released.json()["fronting_private_activates_at"] is None


def test_fronting_guard_release_gets_full_window_with_older_pending_grant(
    auth_client: httpx.Client,
):
    _go_public(auth_client)
    _attest(auth_client)
    member = _member(auth_client, "PendingPath", privacy="public")
    auth_client.patch(
        f"/v1/members/{member}", json={"fronting_private": True}
    )
    vid = _view(auth_client, include_fronting=True)
    auth_client.post(
        f"/v1/share-views/{vid}/members", json={"member_id": member}
    )
    _arm_visibility_safety(auth_client)
    created = auth_client.post(
        "/v1/share-grants",
        json={
            "view_id": vid,
            "subject_type": "public",
            "password": "testpassword123",
        },
    )
    assert created.status_code == 201, created.text
    grant = created.json()["grant"]
    _set_grant_activation_soon(grant["id"])

    before = datetime.now(UTC)
    staged = auth_client.patch(
        f"/v1/members/{member}",
        json={"fronting_private": False, "password": "testpassword123"},
    )
    assert staged.status_code == 200, staged.text
    body = staged.json()
    assert body["fronting_private"] is True
    activates_at = datetime.fromisoformat(body["fronting_private_activates_at"])
    assert activates_at > before + timedelta(days=6, hours=23)


def test_restoring_fronting_guard_cancels_pending_release(
    auth_client: httpx.Client,
):
    vid = _shared_view(auth_client, include_fronting=True)
    member = _member(auth_client, "StillGuarded", privacy="public")
    auth_client.post(
        f"/v1/share-views/{vid}/members", json={"member_id": member}
    )
    auth_client.patch(
        f"/v1/members/{member}", json={"fronting_private": True}
    )
    _arm_visibility_safety(auth_client)
    auth_client.patch(
        f"/v1/members/{member}",
        json={"fronting_private": False, "password": "testpassword123"},
    )

    cancelled = auth_client.patch(
        f"/v1/members/{member}", json={"fronting_private": True}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["fronting_private"] is True
    assert cancelled.json()["fronting_private_activates_at"] is None


# member.privacy: raising a member to public is an exposure too


def test_privacy_flip_to_public_in_a_shared_view_is_deferred(
    auth_client: httpx.Client,
):
    vid = _shared_view(auth_client)
    m = _member(auth_client, "Riser")  # private by default
    assert (
        auth_client.post(
            f"/v1/share-views/{vid}/members", json={"member_id": m}
        ).status_code
        == 200
    )
    _arm_visibility_safety(auth_client)

    denied = auth_client.patch(f"/v1/members/{m}", json={"privacy": "public"})
    assert denied.status_code in (400, 403), denied.text

    ok = auth_client.patch(
        f"/v1/members/{m}", json={"privacy": "public", "password": "testpassword123"}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["privacy"] == "public"

    # The flip landed, the exposure did not: the membership row is back to
    # pending and waits out the window.
    row = auth_client.get(f"/v1/share-views/{vid}").json()["members"][0]
    assert row["status"] == "pending"
    assert row["activates_at"] is not None


def test_privacy_flip_outside_a_shared_view_is_ungated(auth_client: httpx.Client):
    """No grant points at the view, so nothing is exposed and nothing waits."""
    _arm_visibility_safety(auth_client)
    vid = _view(auth_client)
    m = _member(auth_client, "Unpublished")
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": m})

    r = auth_client.patch(f"/v1/members/{m}", json={"privacy": "public"})
    assert r.status_code == 200, r.text
    assert r.json()["privacy"] == "public"
    assert auth_client.get(f"/v1/share-views/{vid}").json()["members"][0][
        "status"
    ] == "active"


def test_privacy_tightening_is_always_immediate(auth_client: httpx.Client):
    vid = _shared_view(auth_client)
    m = _member(auth_client, "Dropper", privacy="public")
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
    _arm_visibility_safety(auth_client)

    # No credential, no waiting - and private -> friends is ungated too, since
    # the friends tier exposes nothing today.
    down = auth_client.patch(f"/v1/members/{m}", json={"privacy": "private"})
    assert down.status_code == 200, down.text
    assert down.json()["privacy"] == "private"

    sideways = auth_client.patch(f"/v1/members/{m}", json={"privacy": "friends"})
    assert sideways.status_code == 200, sideways.text
    assert sideways.json()["privacy"] == "friends"


# ---------------------------------------------------------------------------
# Expired grants: dead everywhere the owner side asks "is this live?"
# ---------------------------------------------------------------------------


def _lapsed_grant_view(c: httpx.Client) -> tuple[str, str]:
    """A view whose only grant expired a minute ago. Returns (view, grant)."""
    _go_public(c)
    _attest(c)
    vid = _view(c, f"Lapsed-{uuid.uuid4().hex[:6]}")
    r = c.post(
        "/v1/share-grants",
        json={
            "view_id": vid,
            "subject_type": "link",
            "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
    )
    assert r.status_code == 201, r.text
    return vid, r.json()["grant"]["id"]


def test_expired_grant_leaves_its_view_unshared(auth_client: httpx.Client):
    vid, gid = _lapsed_grant_view(auth_client)

    assert auth_client.get(f"/v1/share-views/{vid}").json()["is_shared"] is False
    listed = auth_client.get("/v1/share-views").json()
    assert next(v for v in listed if v["id"] == vid)["is_shared"] is False

    # Still listed, though: the owner set that expiry and the client labels it.
    grants = auth_client.get("/v1/share-grants").json()
    assert any(g["id"] == gid and g["expires_at"] for g in grants)


def test_privacy_raise_with_only_an_expired_grant_is_ungated(
    auth_client: httpx.Client,
):
    """Nothing is exposed by the raise, so there is nothing to re-authenticate
    for and nothing to stage."""
    vid, _ = _lapsed_grant_view(auth_client)
    m = _member(auth_client, "RiserAfterExpiry")
    assert (
        auth_client.post(
            f"/v1/share-views/{vid}/members", json={"member_id": m}
        ).status_code
        == 200
    )
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(f"/v1/members/{m}", json={"privacy": "public"})
    assert r.status_code == 200, r.text
    assert r.json()["privacy"] == "public"
    row = auth_client.get(f"/v1/share-views/{vid}").json()["members"][0]
    assert row["status"] == "active"
    assert row["activates_at"] is None


# ---------------------------------------------------------------------------
# Business caps
# ---------------------------------------------------------------------------


def _cap(name: str) -> int:
    """Declared default for a cap, which is what the test stack runs with."""
    from sheaf.config import Settings

    return Settings.model_fields[name].default


def _system_id(c: httpx.Client) -> str:
    return c.get("/v1/systems/me").json()["id"]


def _seed_views(system_id: str, count: int) -> None:
    """Fill view slots straight in the database - the point under test is the
    cap check, not the hundred POSTs it would take to reach it."""

    async def _work(db) -> None:
        from sheaf.models.share import ShareView

        for i in range(count):
            db.add(
                ShareView(
                    id=uuid.uuid4(),
                    system_id=uuid.UUID(system_id),
                    name=f"Seeded {uuid.uuid4().hex[:8]}-{i}",
                )
            )

    _in_db(_work)


def _seed_grants(
    system_id: str,
    view_id: str,
    count: int,
    *,
    revoked: bool = False,
    expired: bool = False,
) -> None:
    async def _work(db) -> None:
        from sheaf.models.share import (
            ShareGrant,
            ShareGrantStatus,
            ShareSubjectType,
        )

        now = datetime.now(UTC)
        for _ in range(count):
            db.add(
                ShareGrant(
                    id=uuid.uuid4(),
                    system_id=uuid.UUID(system_id),
                    view_id=uuid.UUID(view_id),
                    subject_type=ShareSubjectType.LINK.value,
                    token_hash=uuid.uuid4().hex,
                    status=(
                        ShareGrantStatus.REVOKED.value
                        if revoked
                        else ShareGrantStatus.ACTIVE.value
                    ),
                    revoked_at=now if revoked else None,
                    expires_at=now - timedelta(minutes=1) if expired else None,
                    created_at=now,
                )
            )

    _in_db(_work)


def test_share_view_cap_is_enforced(auth_client: httpx.Client):
    _seed_views(_system_id(auth_client), _cap("share_views_max"))

    r = auth_client.post("/v1/share-views", json={"name": "One too many"})
    assert r.status_code == 403, r.text
    assert "share views" in r.text.lower()


def test_share_grant_cap_counts_only_live_grants(auth_client: httpx.Client):
    # Public, so the 403 below is the cap talking and not the privacy ceiling.
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client)
    system_id = _system_id(auth_client)
    cap = _cap("share_grants_max")

    # Revoked grants are not exposure, so they must not use up the budget.
    _seed_grants(system_id, vid, cap, revoked=True)
    ok = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "link"}
    )
    assert ok.status_code == 201, ok.text

    # That one plus these fills it.
    _seed_grants(system_id, vid, cap - 1)
    denied = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "link"}
    )
    assert denied.status_code == 403, denied.text
    assert "revoke" in denied.text.lower()


def test_expired_grant_frees_its_cap_slot(auth_client: httpx.Client):
    """A lapsed link serves nobody, so holding a slot hostage would mean the
    only way back under the cap is noticing and revoking a dead grant."""
    # Public, so the 403 below is the cap talking and not the privacy ceiling.
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client)
    system_id = _system_id(auth_client)
    cap = _cap("share_grants_max")

    _seed_grants(system_id, vid, cap - 1)
    _seed_grants(system_id, vid, 1, expired=True)
    ok = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "link"}
    )
    assert ok.status_code == 201, ok.text

    # And that really did fill the last slot.
    denied = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "link"}
    )
    assert denied.status_code == 403, denied.text


# ---------------------------------------------------------------------------
# Audit surface
# ---------------------------------------------------------------------------


def test_audit_lists_live_grants_only(auth_client: httpx.Client):
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "Audited", include_fronting=True)
    m = _member(auth_client, "AuditMember")
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
    gid = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    ).json()["grant"]["id"]

    audit = auth_client.get("/v1/sharing/audit").json()
    assert len(audit["entries"]) == 1
    entry = audit["entries"][0]
    assert entry["view_name"] == "Audited"
    assert entry["member_count"] == 1
    assert entry["include_fronting"] is True

    # Revoked grants drop off the audit.
    auth_client.delete(f"/v1/share-grants/{gid}")
    assert auth_client.get("/v1/sharing/audit").json()["entries"] == []


def test_audit_group_count_matches_what_is_served(auth_client: httpx.Client):
    """The audit counts groups through the projection's own choke point, so it
    reports what a visitor gets rather than what the flag permits: only public
    groups, only ones with somebody in them this view serves, and none at all
    while the flag is off."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "GroupAudit")
    m = _member(auth_client, "InTheGroup", privacy="public")
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
    shown = _group(auth_client, "Shown")
    assert (
        auth_client.put(
            f"/v1/groups/{shown}/members", json={"member_ids": [m]}
        ).status_code
        == 200
    )
    _group(auth_client, "Hidden")
    assert (
        auth_client.patch(
            f"/v1/groups/{shown}", json={"privacy": "public"}
        ).status_code
        == 200
    )
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )

    entry = auth_client.get("/v1/sharing/audit").json()["entries"][0]
    # Flag off: nothing is served, whatever the groups themselves say.
    assert entry["include_groups"] is False
    assert entry["group_count"] == 0

    auth_client.patch(f"/v1/share-views/{vid}", json={"include_groups": True})
    entry = auth_client.get("/v1/sharing/audit").json()["entries"][0]
    assert entry["include_groups"] is True
    assert entry["group_count"] == 1


def test_audit_does_not_count_a_public_group_with_an_empty_roster(
    auth_client: httpx.Client,
):
    """A public group nobody in this view belongs to is not served, so the
    audit must not count it either - the count and the payload come through one
    function precisely so they cannot disagree."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "EmptyGroupAudit", include_groups=True)
    shown = _member(auth_client, "Shown", privacy="public")
    outside = _member(auth_client, "Outside", privacy="public")
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": shown})
    offstage = _group(auth_client, "Offstage")
    assert (
        auth_client.put(
            f"/v1/groups/{offstage}/members", json={"member_ids": [outside]}
        ).status_code
        == 200
    )
    assert (
        auth_client.patch(
            f"/v1/groups/{offstage}", json={"privacy": "public"}
        ).status_code
        == 200
    )
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )

    entry = auth_client.get("/v1/sharing/audit").json()["entries"][0]
    assert entry["include_groups"] is True
    assert entry["group_count"] == 0


def test_audit_reports_the_member_list_and_permalink_settings(
    auth_client: httpx.Client,
):
    """With the roster off, `member_count` still reports the curation (nothing
    was destroyed) and `include_members` is what says it is not being served.
    The served count goes null rather than zero, for the same reason the public
    payload's does: a roster that is not served must not be countable."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "RosterAudit", include_members=False)
    m = _member(auth_client, "Curated", privacy="public")
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
    auth_client.patch(f"/v1/share-views/{vid}", json={"member_permalinks": True})
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )

    entry = auth_client.get("/v1/sharing/audit").json()["entries"][0]
    assert entry["include_members"] is False
    assert entry["member_count"] == 1
    assert entry["served_member_count"] is None
    assert entry["member_permalinks"] is True


def test_audit_served_member_count_is_what_visitors_get(
    auth_client: httpx.Client,
):
    """`member_count` is curation, `served_member_count` is exposure, and the
    audit reports both because they answer different questions. Every reason a
    member drops off the page - their privacy, the archive, a queued delete -
    moves the second number without touching the first."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "ServedCount")
    shown = _member(auth_client, "Shown", privacy="public")
    private = _member(auth_client, "Private", privacy="private")
    archived = _member(auth_client, "Archived", privacy="public")
    for m in (shown, private, archived):
        assert (
            auth_client.post(
                f"/v1/share-views/{vid}/members", json={"member_id": m}
            ).status_code
            == 200
        )
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )

    entry = auth_client.get("/v1/sharing/audit").json()["entries"][0]
    assert entry["member_count"] == 3
    assert entry["served_member_count"] == 2

    assert auth_client.post(f"/v1/members/{archived}/archive").status_code == 200
    entry = auth_client.get("/v1/sharing/audit").json()["entries"][0]
    # The curation is untouched by archiving; the exposure is not.
    assert entry["member_count"] == 3
    assert entry["served_member_count"] == 1


def test_view_rows_say_who_is_actually_being_served(auth_client: httpx.Client):
    """Each curated member row carries the projection's own answer to "does
    this person appear?", with a named reason when they do not. The client used
    to infer this from member privacy alone, which missed archiving and a
    queued deletion - both of which drop a member from the page at once."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "RowStates")
    shown = _member(auth_client, "Shown", privacy="public")
    private = _member(auth_client, "Private", privacy="private")
    archived = _member(auth_client, "Archived", privacy="public")
    for m in (shown, private, archived):
        auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
    assert auth_client.post(f"/v1/members/{archived}/archive").status_code == 200

    rows = auth_client.get(f"/v1/share-views/{vid}").json()["members"]
    by_member = {r["member_id"]: r for r in rows}
    assert by_member[shown]["served"] is True
    assert by_member[shown]["not_served_reason"] is None
    assert by_member[private]["served"] is False
    assert by_member[private]["not_served_reason"] == "private"
    assert by_member[archived]["served"] is False
    assert by_member[archived]["not_served_reason"] == "archived"


def test_view_rows_report_a_queued_deletion_as_not_served(
    auth_client: httpx.Client,
):
    """Deleting a member is held for the grace window so the owner can change
    their mind - the member stops being published at once, and the sharing
    screen has to say so rather than showing them as live."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "QueuedDelete")
    doomed = _member(auth_client, "Doomed", privacy="public")
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": doomed})
    # The `members` category is what holds a member delete for the window; the
    # profile-visibility one governs exposure and is a different switch.
    assert (
        auth_client.patch(
            "/v1/system/safety",
            json={
                "grace_period_days": 7,
                "applies_to_members": True,
                "auth_tier": "password",
            },
        ).status_code
        == 200
    )

    r = auth_client.request(
        "DELETE",
        f"/v1/members/{doomed}",
        json={"password": "testpassword123"},
    )
    assert r.status_code == 202, r.text

    row = next(
        row
        for row in auth_client.get(f"/v1/share-views/{vid}").json()["members"]
        if row["member_id"] == doomed
    )
    assert row["served"] is False
    assert row["not_served_reason"] == "deletion_queued"


def test_view_rows_report_a_member_still_inside_the_grace_window(
    auth_client: httpx.Client,
):
    """A member added to an already-shared view waits out the window. That is
    not "held back" - it resolves by itself - so it gets its own reason and the
    row's own `pending` status still says the same thing beside it."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "PendingRow")
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    _arm_visibility_safety(auth_client)
    m = _member(auth_client, "Waiting", privacy="public")
    r = auth_client.post(
        f"/v1/share-views/{vid}/members",
        json={"member_id": m, "password": "testpassword123"},
    )
    assert r.status_code == 200, r.text

    row = next(
        row
        for row in r.json()["members"]
        if row["member_id"] == m
    )
    assert row["status"] == "pending"
    assert row["served"] is False
    assert row["not_served_reason"] == "pending"


def test_a_mixed_share_view_patch_is_refused(auth_client: httpx.Client):
    """One body may not both loosen and tighten. The loosening needs a step-up,
    and if that step-up failed the tightening would fail with it - which would
    put a gate in front of going dark, the one thing nothing may do."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "Mixed", include_bio=True)
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(
        f"/v1/share-views/{vid}",
        json={
            "include_fronting": True,
            "include_bio": False,
            "password": "testpassword123",
        },
    )
    assert r.status_code == 400, r.text
    assert "separate requests" in r.json()["detail"]

    # The tightening on its own is ungated and lands immediately, which is the
    # whole point of refusing to bundle it with the raise.
    r = auth_client.patch(f"/v1/share-views/{vid}", json={"include_bio": False})
    assert r.status_code == 200, r.text
    assert r.json()["include_bio"] is False


def test_an_unexposing_share_view_patch_may_still_flip_several_flags(
    auth_client: httpx.Client,
):
    """The refusal is about MIXING directions, not about touching more than one
    flag: turning three things off at once is still one un-exposing act and
    must stay ungated."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(
        auth_client,
        "AllOff",
        include_bio=True,
        include_fronting=True,
        include_groups=True,
    )
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(
        f"/v1/share-views/{vid}",
        json={
            "include_bio": False,
            "include_fronting": False,
            "include_groups": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["include_bio"] is False
    assert body["include_fronting"] is False
    assert body["include_groups"] is False


def test_releasing_never_shareable_is_gated_like_the_fronting_guard(
    auth_client: httpx.Client,
):
    """Clearing the hardest share guard must not be easier than clearing the
    softer one beside it. It used to fall through to a plain write with no
    step-up at all, which made it the cheap way past the other gates."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "GuardRelease", include_fronting=True)
    m = _member(auth_client, "Secret", privacy="public")
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    _mark_never_shareable(auth_client, m)
    _arm_visibility_safety(auth_client)

    # No credentials: refused, exactly as releasing fronting_private is.
    r = auth_client.patch(f"/v1/members/{m}", json={"never_shareable": False})
    assert r.status_code in (400, 403), r.text

    r = auth_client.patch(
        f"/v1/members/{m}",
        json={"never_shareable": False, "password": "testpassword123"},
    )
    assert r.status_code == 200, r.text
    # No staging column for this guard, so the re-auth IS the gate and the
    # release lands at once rather than waiting out the window.
    assert r.json()["never_shareable"] is False


def test_setting_never_shareable_is_never_gated(auth_client: httpx.Client):
    """The other direction is un-exposing, so it stays instant and ungated even
    with the category armed - nothing may slow down going dark."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "GuardSet", include_fronting=True)
    m = _member(auth_client, "Ordinary", privacy="public")
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(f"/v1/members/{m}", json={"never_shareable": True})
    assert r.status_code == 200, r.text
    assert r.json()["never_shareable"] is True


def test_a_mixed_member_patch_is_refused(auth_client: httpx.Client):
    """Same rule on the member editor's three exposure settings: a body that
    raises one and lowers another is refused before the step-up runs, so a
    failed password can never take the lowering down with it."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "MixedMember", include_fronting=True)
    m = _member(auth_client, "Both", privacy="private")
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(
        f"/v1/members/{m}",
        json={
            "privacy": "public",
            "fronting_private": True,
            "password": "testpassword123",
        },
    )
    assert r.status_code == 400, r.text
    assert "separate requests" in r.json()["detail"]

    # Sent on its own, the lowering needs nothing and lands at once.
    r = auth_client.patch(f"/v1/members/{m}", json={"fronting_private": True})
    assert r.status_code == 200, r.text
    assert r.json()["fronting_private"] is True


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_cannot_touch_another_systems_view(auth_client: httpx.Client):
    other = _second_client()
    try:
        their_view = _view(other, "Theirs")
    finally:
        pass

    # Same 404 for get / patch / delete / add-member: no existence oracle.
    assert auth_client.get(f"/v1/share-views/{their_view}").status_code == 404
    assert (
        auth_client.patch(
            f"/v1/share-views/{their_view}", json={"name": "hijack"}
        ).status_code
        == 404
    )
    assert auth_client.delete(f"/v1/share-views/{their_view}").status_code == 404
    my_member = _member(auth_client, "Mine")
    assert (
        auth_client.post(
            f"/v1/share-views/{their_view}/members", json={"member_id": my_member}
        ).status_code
        == 404
    )
    other.close()


def test_cannot_add_foreign_member_to_own_view(auth_client: httpx.Client):
    other = _second_client()
    their_member = _member(other, "TheirMember")
    other.close()

    vid = _view(auth_client, "MyView")
    r = auth_client.post(
        f"/v1/share-views/{vid}/members", json={"member_id": their_member}
    )
    # Foreign member is invisible to our system -> 404, never added.
    assert r.status_code == 404


def test_cannot_revoke_another_systems_grant(auth_client: httpx.Client):
    other = _second_client()
    # The other system publishes, so it needs its own privacy flip.
    _go_public(other)
    _attest(other)
    their_view = _view(other, "TheirShared")
    their_grant = other.post(
        "/v1/share-grants", json={"view_id": their_view, "subject_type": "public"}
    ).json()["grant"]["id"]

    assert auth_client.delete(f"/v1/share-grants/{their_grant}").status_code == 404
    # Still live for its real owner.
    assert any(
        g["id"] == their_grant and g["status"] != "revoked"
        for g in other.get("/v1/share-grants").json()
    )
    other.close()


# ---------------------------------------------------------------------------
# System privacy is the ceiling: nothing is minted under a private system
# ---------------------------------------------------------------------------


def test_publishing_is_refused_while_the_system_is_private(
    auth_client: httpx.Client,
):
    """Refused rather than minted-and-suppressed. A grant that serves nobody
    is not safer for being useless: the owner walks the whole flow, hands out
    a link, and gets a 404 with nothing telling them why."""
    _attest(auth_client)
    vid = _view(auth_client)
    r = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    assert r.status_code == 400, r.text
    # The message has to name the fix; it is the owner's own setting.
    detail = r.json()["detail"]
    assert "public" in detail and "system" in detail.lower()
    assert auth_client.get("/v1/share-grants").json() == []


def test_publishing_a_link_is_refused_too(auth_client: httpx.Client):
    """An unlisted link is not a lesser tier that slips under the ceiling."""
    _attest(auth_client)
    vid = _view(auth_client)
    r = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "link"}
    )
    assert r.status_code == 400, r.text


def test_publishing_is_refused_at_friends_privacy(auth_client: httpx.Client):
    _attest(auth_client)
    auth_client.patch("/v1/systems/me", json={"privacy": "friends"})
    vid = _view(auth_client)
    r = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    assert r.status_code == 400, r.text


def test_going_private_later_does_not_revoke_anything(auth_client: httpx.Client):
    """The decided semantic: system privacy suppresses, it never revokes. The
    owner's curation survives a switch they may be flipping for an afternoon."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client)
    gid = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    ).json()["grant"]["id"]

    auth_client.patch("/v1/systems/me", json={"privacy": "private"})

    grant = next(
        g for g in auth_client.get("/v1/share-grants").json() if g["id"] == gid
    )
    assert grant["revoked_at"] is None
    assert grant["status"] != "revoked"


# ---------------------------------------------------------------------------
# The audit says WHY the page is dark
# ---------------------------------------------------------------------------


def test_audit_reports_nothing_suppressed_while_serving(auth_client: httpx.Client):
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client)
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )

    audit = auth_client.get("/v1/sharing/audit").json()
    assert audit["profile_suppressed"] is None
    assert len(audit["entries"]) == 1


def test_audit_reports_a_private_system(auth_client: httpx.Client):
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client)
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    auth_client.patch("/v1/systems/me", json={"privacy": "private"})

    audit = auth_client.get("/v1/sharing/audit").json()
    assert audit["profile_suppressed"] == "system_private"
    # The entries stay, and stay accurate: they describe curation that is
    # intact, which is exactly why the reason has to be reported beside them.
    assert len(audit["entries"]) == 1


def test_audit_reports_an_operator_takedown_above_everything_else(
    auth_client: httpx.Client,
):
    """`publishing_blocked` outranks the other reasons, and unlike them it is
    named. The owner already sees the latch on their own system read, and it is
    the one reason here they cannot clear themselves - pointing them at the
    privacy switch instead would send them to a control that just 403s."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client)
    auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    system_id = auth_client.get("/v1/systems/me").json()["id"]

    async def _work(db) -> None:
        from sheaf.models.system import System

        system = await db.get(System, uuid.UUID(system_id))
        assert system is not None
        system.publishing_blocked = True

    _in_db(_work)

    audit = auth_client.get("/v1/sharing/audit").json()
    assert audit["profile_suppressed"] == "publishing_blocked"
    assert len(audit["entries"]) == 1

    # Still the latch's answer once the owner also goes private: the switch
    # they can flip is not the one keeping them dark.
    auth_client.patch("/v1/systems/me", json={"privacy": "private"})
    audit = auth_client.get("/v1/sharing/audit").json()
    assert audit["profile_suppressed"] == "publishing_blocked"


def _set_account_status(email: str, status: str) -> None:
    async def _work(db) -> None:
        from sqlalchemy import select

        from sheaf.crypto import blind_index
        from sheaf.models.user import User

        row = await db.execute(
            select(User).where(User.email_hash == blind_index(email))
        )
        row.scalar_one().account_status = status

    _in_db(_work)


def test_audit_reports_an_account_state_without_naming_it():
    """Coarse on purpose. The owner was already told at login which state they
    are in and why; a finer value here would only be one more thing that could
    end up somewhere it should not."""
    c = httpx.Client(base_url=BASE_URL)
    email = f"aud-{uuid.uuid4().hex[:8]}@sheaf.dev"
    r = c.post(
        "/v1/auth/register", json={"email": email, "password": "testpassword123"}
    )
    assert r.status_code == 201, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    _go_public(c)
    _attest(c)
    vid = _view(c)
    c.post("/v1/share-grants", json={"view_id": vid, "subject_type": "public"})

    _set_account_status(email, "pending_deletion")
    audit = c.get("/v1/sharing/audit").json()
    assert audit["profile_suppressed"] == "account_state"
    assert "deletion" not in str(audit).lower()
    c.close()


# ---------------------------------------------------------------------------
# An account on its way out can still go dark, but cannot build
# ---------------------------------------------------------------------------


def _pending_deletion_client() -> tuple[httpx.Client, str]:
    c = httpx.Client(base_url=BASE_URL)
    email = f"pd-{uuid.uuid4().hex[:8]}@sheaf.dev"
    r = c.post(
        "/v1/auth/register", json={"email": email, "password": "testpassword123"}
    )
    assert r.status_code == 201, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    _go_public(c)
    _attest(c)
    return c, email


def test_pending_deletion_blocks_publishing_and_widening():
    c, email = _pending_deletion_client()
    m = _member(c, "Late", privacy="public")
    vid = _view(c)
    gid = c.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    ).json()["grant"]["id"]

    _set_account_status(email, "pending_deletion")

    # Every exposing mutation on the sharing router: a second grant, a new
    # view, a member/group/field added to one, and turning an exposure flag on.
    assert c.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "link"}
    ).status_code == 409
    assert c.post("/v1/share-views", json={"name": "New"}).status_code == 409
    assert c.post(
        f"/v1/share-views/{vid}/members", json={"member_id": m}
    ).status_code == 409
    assert c.patch(
        f"/v1/share-views/{vid}", json={"include_bio": True}
    ).status_code == 409

    # And the ways out stay open to the last minute. Nothing, including this
    # check, may stand between somebody and going dark.
    assert c.patch(
        f"/v1/share-views/{vid}", json={"include_members": False}
    ).status_code == 200
    assert c.delete(f"/v1/share-grants/{gid}").status_code == 204
    c.close()


def test_pending_deletion_still_allows_rotating_a_link():
    """Rotation is how somebody cuts off a link that has spread further than
    they meant it to. It publishes nothing new, so it is not blocked."""
    c, email = _pending_deletion_client()
    vid = _view(c)
    gid = c.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "link"}
    ).json()["grant"]["id"]

    _set_account_status(email, "pending_deletion")

    rotated = c.post(f"/v1/share-grants/{gid}/rotate")
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["token"]
    c.close()


# ---------------------------------------------------------------------------
# Exposure-audit trail: every RAISE leaves a queryable SecurityEvent, every
# un-exposing act leaves none, and the event detail never carries member content
# ---------------------------------------------------------------------------


def _logged_client() -> tuple[httpx.Client, str]:
    """A fresh authed client whose email we keep, so its security-event rows
    can be read back by user_id."""
    c = httpx.Client(base_url=BASE_URL)
    email = f"explog-{uuid.uuid4().hex[:8]}@sheaf.dev"
    r = c.post(
        "/v1/auth/register", json={"email": email, "password": "testpassword123"}
    )
    assert r.status_code == 201, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c, email


def _exposure_events(email: str) -> list[dict]:
    """Every EXPOSURE_RAISED event for this account, newest first, as
    {outcome, detail} dicts. Read straight from the table - the owner API does
    not surface security events, and the row is committed inline before the
    request that raised it returns."""
    out: list[dict] = []

    async def _work(db) -> None:
        from sqlalchemy import select

        from sheaf.crypto import blind_index
        from sheaf.models.security_event import SecurityEvent, SecurityEventType
        from sheaf.models.user import User

        user = (
            await db.execute(
                select(User).where(User.email_hash == blind_index(email))
            )
        ).scalar_one()
        rows = (
            (
                await db.execute(
                    select(SecurityEvent)
                    .where(
                        SecurityEvent.user_id == user.id,
                        SecurityEvent.event_type
                        == SecurityEventType.EXPOSURE_RAISED,
                    )
                    .order_by(SecurityEvent.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            out.append({"outcome": r.outcome, "detail": r.detail})

    _in_db(_work)
    return out


def _events_of(email: str, source: str) -> list[dict]:
    """EXPOSURE_RAISED events for one `detail.source`, newest first. Lets a test
    ignore the system_privacy event that `_go_public` emits as a side effect."""
    return [e for e in _exposure_events(email) if e["detail"]["source"] == source]


def _backdate_view_members(view_id: str) -> None:
    """Make a view's staged (PENDING) membership rows due for the finalize job."""

    async def _work(db) -> None:
        from sqlalchemy import select

        from sheaf.models.share import ShareViewMember

        rows = (
            (
                await db.execute(
                    select(ShareViewMember).where(
                        ShareViewMember.view_id == uuid.UUID(view_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            if row.activates_at is not None:
                row.activates_at = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)


def test_system_privacy_flip_to_public_is_logged_immediate():
    """The master switch to public is the #1 exposure, so it records an
    EXPOSURE_RAISED. With no grace window it lands live: outcome `immediate`."""
    c, email = _logged_client()
    _go_public(c)

    events = _exposure_events(email)
    assert len(events) == 1, events
    assert events[0]["outcome"] == "immediate"
    assert events[0]["detail"]["source"] == "system_privacy"
    c.close()


def test_lowering_system_privacy_records_nothing():
    """Going back to private un-exposes, and un-exposing is never audited here."""
    c, email = _logged_client()
    _go_public(c)
    before = len(_exposure_events(email))

    r = c.patch("/v1/systems/me", json={"privacy": "private"})
    assert r.status_code == 200, r.text
    assert len(_exposure_events(email)) == before
    c.close()


def test_staged_system_privacy_flip_is_logged_staged():
    """A raise that would actually serve a grant parks pending behind the grace
    window; the event says `staged`. Needs a live grant to exist, so publish
    once, drop back to private (the grant goes dormant, not revoked), then raise
    again with the window armed."""
    c, email = _logged_client()
    _shared_view(c)  # go public + a live public grant

    assert (
        c.patch("/v1/systems/me", json={"privacy": "private"}).status_code == 200
    )
    _arm_visibility_safety(c)

    r = c.patch(
        "/v1/systems/me",
        json={"privacy": "public", "password": "testpassword123"},
    )
    assert r.status_code == 200, r.text

    # Newest event is the staged raise (an earlier immediate one came from the
    # first publish).
    events = _exposure_events(email)
    assert events[0]["outcome"] == "staged"
    assert events[0]["detail"]["source"] == "system_privacy"
    c.close()


def test_view_flag_loosening_is_logged_and_turning_it_off_is_not():
    """Turning an exposure flag on for a shared view is a raise; turning it back
    off is a tightening and records nothing."""
    c, email = _logged_client()
    vid = _shared_view(c)
    _arm_visibility_safety(c)

    on = c.patch(
        f"/v1/share-views/{vid}",
        json={"include_bio": True, "password": "testpassword123"},
    )
    assert on.status_code == 200, on.text

    flag_events = _events_of(email, "view_flags")
    assert [e["outcome"] for e in flag_events] == ["staged"]
    assert "include_bio" in flag_events[0]["detail"]["flags"]

    # Turning it back off (a tightening) is ungated and adds no new event.
    off = c.patch(f"/v1/share-views/{vid}", json={"include_bio": False})
    assert off.status_code == 200, off.text
    assert len(_events_of(email, "view_flags")) == 1
    c.close()


def test_adding_a_member_logs_only_when_the_view_is_shared():
    """Adding a member to an already-shared view exposes them; curating an
    unshared view publishes nobody and stays silent."""
    c, email = _logged_client()

    # Unshared view: no grant -> no event.
    quiet_vid = _view(c, "Unshared")
    m1 = _member(c, "Curated", privacy="public")
    r = c.post(f"/v1/share-views/{quiet_vid}/members", json={"member_id": m1})
    assert r.status_code == 200, r.text
    assert _events_of(email, "view_member") == []

    # Shared view: the add is a raise.
    shared_vid = _shared_view(c)
    m2 = _member(c, "Exposed", privacy="public")
    r = c.post(f"/v1/share-views/{shared_vid}/members", json={"member_id": m2})
    assert r.status_code == 200, r.text

    member_events = _events_of(email, "view_member")
    assert [e["outcome"] for e in member_events] == ["immediate"]
    assert member_events[0]["detail"]["member_id"] == m2
    c.close()


def test_finalize_sweep_logs_an_activation_that_correlates_with_the_stage(
    admin_client: httpx.Client,
):
    """A staged member add records `staged` at request time and `activated` when
    the finalize sweep promotes it, both under the same source and member id."""
    c, email = _logged_client()
    shared_vid = _shared_view(c)
    _arm_visibility_safety(c)
    member_id = _member(c, "LaterPublic", privacy="public")

    staged = c.post(
        f"/v1/share-views/{shared_vid}/members",
        json={"member_id": member_id, "password": "testpassword123"},
    )
    assert staged.status_code == 200, staged.text
    member_events = _events_of(email, "view_member")
    assert [e["outcome"] for e in member_events] == ["staged"]

    _backdate_view_members(shared_vid)
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "success"

    member_events = _events_of(email, "view_member")
    outcomes = [e["outcome"] for e in member_events]
    assert "activated" in outcomes and "staged" in outcomes, member_events
    activated = next(e for e in member_events if e["outcome"] == "activated")
    assert activated["detail"]["member_id"] == member_id
    c.close()


def test_exposure_event_detail_carries_no_member_content():
    """The detail JSON is ids/flags/booleans only - never a decrypted name."""
    c, email = _logged_client()
    secret_name = f"SecretName-{uuid.uuid4().hex[:8]}"
    shared_vid = _shared_view(c)
    member_id = _member(c, secret_name, privacy="public")

    r = c.post(
        f"/v1/share-views/{shared_vid}/members", json={"member_id": member_id}
    )
    assert r.status_code == 200, r.text

    events = _exposure_events(email)
    assert events, "expected an exposure event to inspect"
    for e in events:
        assert secret_name not in str(e["detail"])
    c.close()


def test_raising_a_member_to_public_logs_but_lowering_does_not():
    """A member ceiling raise records; dropping it back to private does not."""
    c, email = _logged_client()
    shared_vid = _shared_view(c)
    member_id = _member(c, "Ceiling", privacy="private")
    # Put them in the shared view first (private, so not yet served).
    assert (
        c.post(
            f"/v1/share-views/{shared_vid}/members", json={"member_id": member_id}
        ).status_code
        == 200
    )
    before = len(_exposure_events(email))

    raised = c.patch(f"/v1/members/{member_id}", json={"privacy": "public"})
    assert raised.status_code == 200, raised.text
    after_raise = _exposure_events(email)
    assert len(after_raise) == before + 1
    assert after_raise[0]["detail"]["source"] == "member_privacy"
    assert after_raise[0]["detail"]["privacy_raise"] is True

    lowered = c.patch(f"/v1/members/{member_id}", json={"privacy": "private"})
    assert lowered.status_code == 200, lowered.text
    assert len(_exposure_events(email)) == len(after_raise)
    c.close()


# ---------------------------------------------------------------------------
# The instance's public surface switched off
#
# All marked `public_profiles_off` and therefore run on the one config that
# serves no public surface. The scenario they describe is the ordinary one: an
# operator turned the setting off (or never had it on) while grants already
# existed. The setting stops the serving; it does not revoke anything, so the
# owner-side API has to keep every way OUT open while refusing every way in.
#
# "Every way in" is the whole surface, not just publishing. Staging a member
# into an already-granted view does not wait for the switch - the finalize sweep
# promotes the row on its own schedule - so the member would be sitting on a
# live page the day an operator turns the surface back on, with nobody present
# to agree to it. A dormant-but-live view is the same wake-up hazard as a
# dormant grant, so creating a view, adding a member/field/group (re-sync
# included) and turning a flag on are all refused alongside minting a grant.
# ---------------------------------------------------------------------------


def _grant_row_in_db(system_id: str, view_id: str, subject_type: str) -> str:
    """Write a live grant straight into the database.

    The one thing these tests cannot ask the API for, which is the entire
    point: with the public surface off, publishing is refused, so a grant made
    while it was ON has to be simulated. Same shape `create_grant` writes for
    an unsafeguarded system - ACTIVE, no expiry, no token needed for a public
    grant.
    """
    grant_id = str(uuid.uuid4())

    async def _work(db) -> None:
        from sheaf.crypto import hash_share_token
        from sheaf.models.share import ShareGrant, ShareGrantStatus

        db.add(
            ShareGrant(
                id=uuid.UUID(grant_id),
                system_id=uuid.UUID(system_id),
                view_id=uuid.UUID(view_id),
                subject_type=subject_type,
                token_hash=(
                    hash_share_token(f"tok-{grant_id}")
                    if subject_type == "link"
                    else None
                ),
                status=ShareGrantStatus.ACTIVE.value,
                created_at=datetime.now(UTC),
            )
        )

    _in_db(_work)
    return grant_id


def _view_row_in_db(system_id: str, name: str, **flags) -> str:
    """Write a share view straight into the database.

    Needed for the same reason `_grant_row_in_db` is: with the surface off the
    API refuses to create a view, so a view that was built while the surface was
    ON has to be simulated rather than asked for.
    """
    view_id = str(uuid.uuid4())

    async def _work(db) -> None:
        from sheaf.models.share import ShareView

        db.add(
            ShareView(
                id=uuid.UUID(view_id),
                system_id=uuid.UUID(system_id),
                name=name,
                **flags,
            )
        )

    _in_db(_work)
    return view_id


def _stock_view_in_db(
    view_id: str,
    *,
    member_id: str | None = None,
    field_id: str | None = None,
    group_id: str | None = None,
    member_pending: bool = False,
) -> None:
    """Put member/field/group rows into a view straight through the database.

    Same reason again: the API refuses every add while the surface is off, so a
    view curated back when it was on has to be written directly. With
    `member_pending` the membership lands PENDING with its window already
    elapsed - exactly the row the finalize sweep exists to promote, staged
    before the switch was ever turned off.
    """

    async def _work(db) -> None:
        from sheaf.models.share import (
            ShareItemStatus,
            ShareViewField,
            ShareViewGroup,
            ShareViewMember,
        )

        now = datetime.now(UTC)
        if member_id is not None:
            db.add(
                ShareViewMember(
                    id=uuid.uuid4(),
                    view_id=uuid.UUID(view_id),
                    member_id=uuid.UUID(member_id),
                    status=(
                        ShareItemStatus.PENDING.value
                        if member_pending
                        else ShareItemStatus.ACTIVE.value
                    ),
                    activates_at=(
                        now - timedelta(minutes=1) if member_pending else None
                    ),
                    created_at=now,
                )
            )
        if field_id is not None:
            db.add(
                ShareViewField(
                    id=uuid.uuid4(),
                    view_id=uuid.UUID(view_id),
                    field_id=uuid.UUID(field_id),
                    status=ShareItemStatus.ACTIVE.value,
                    created_at=now,
                )
            )
        if group_id is not None:
            db.add(
                ShareViewGroup(
                    id=uuid.uuid4(),
                    view_id=uuid.UUID(view_id),
                    group_id=uuid.UUID(group_id),
                    synced_at=now,
                    created_at=now,
                )
            )

    _in_db(_work)


def _dormant_setup(c: httpx.Client, subject_type: str = "public") -> tuple[str, str]:
    """A view with a dormant grant pointing at it. Returns (view_id, grant_id).

    Both rows go in through the database, because both acts are now refused
    while the surface is off - which is the state these tests describe: a view
    and a grant that were made while it was ON and are sitting dormant.
    """
    _go_public(c)
    _attest(c)
    system_id = c.get("/v1/systems/me").json()["id"]
    vid = _view_row_in_db(
        system_id, f"Dormant-{uuid.uuid4().hex[:6]}", include_bio=False
    )
    return vid, _grant_row_in_db(system_id, vid, subject_type)


@pytest.mark.public_profiles_off
def test_publishing_is_refused_while_the_instance_surface_is_off(
    auth_client: httpx.Client,
):
    """The trap this closes: a grant minted now serves nobody today and would
    start serving the day an operator flips the setting back, with nobody left
    who remembers agreeing to it."""
    _go_public(auth_client)
    _attest(auth_client)
    # View creation is itself refused while the surface is off, so the view
    # under test is written straight into the database.
    system_id = auth_client.get("/v1/systems/me").json()["id"]
    vid = _view_row_in_db(system_id, f"Off-{uuid.uuid4().hex[:6]}")

    for subject in ("public", "link"):
        r = auth_client.post(
            "/v1/share-grants", json={"view_id": vid, "subject_type": subject}
        )
        assert r.status_code == 403, r.text
        # The detail has to say both halves: nothing new, and nothing lost.
        assert "turned off" in r.json()["detail"]
        assert "unpublishing" in r.json()["detail"].lower()

    assert auth_client.get("/v1/share-grants").json() == []


@pytest.mark.public_profiles_off
def test_creating_a_view_is_refused_with_the_surface_off(
    auth_client: httpx.Client,
):
    """"Selection is not publication" was only half true, and this is the half
    that bites: a view built now is a fully-curated view sitting one grant away
    from serving on whatever day an operator turns the surface back on."""
    r = auth_client.post(
        "/v1/share-views", json={"name": f"Prepared-{uuid.uuid4().hex[:6]}"}
    )
    assert r.status_code == 403, r.text
    assert "turned off" in r.json()["detail"]
    assert auth_client.get("/v1/share-views").json() == []


@pytest.mark.public_profiles_off
def test_adding_to_a_view_is_refused_with_the_surface_off(
    auth_client: httpx.Client,
):
    """The wake-up hazard this closes: a row staged into an already-granted view
    is promoted by the finalize sweep on its own schedule, so the member, field
    or group would be on a live page the day the switch comes back."""
    vid, _ = _dormant_setup(auth_client)
    m = _member(auth_client, "Prep", privacy="public")
    g = _group(auth_client, f"G-{uuid.uuid4().hex[:6]}")
    f = _field(auth_client, f"F-{uuid.uuid4().hex[:6]}")

    for path, body in (
        (f"/v1/share-views/{vid}/members", {"member_id": m}),
        (f"/v1/share-views/{vid}/groups", {"group_id": g}),
        (f"/v1/share-views/{vid}/fields", {"field_id": f}),
    ):
        r = auth_client.post(path, json=body)
        assert r.status_code == 403, f"{path}: {r.text}"
        assert "turned off" in r.json()["detail"]

    got = auth_client.get(f"/v1/share-views/{vid}").json()
    assert got["members"] == []
    assert got["fields"] == []
    assert got["groups"] == []


@pytest.mark.public_profiles_off
def test_group_resync_is_refused_with_the_surface_off(auth_client: httpx.Client):
    """The re-sync is the same endpoint and the same refusal, and it is the one
    worth a test of its own: re-posting expands the group's CURRENT roster, so
    somebody who joined the group after the switch went off would otherwise be
    pulled into a dormant-but-live view."""
    vid, _ = _dormant_setup(auth_client)
    g = _group(auth_client, f"G-{uuid.uuid4().hex[:6]}")
    _stock_view_in_db(vid, group_id=g)

    joined_later = _member(auth_client, "JoinedLater", privacy="public")
    assert auth_client.put(
        f"/v1/groups/{g}/members", json={"member_ids": [joined_later]}
    ).status_code == 200

    r = auth_client.post(f"/v1/share-views/{vid}/groups", json={"group_id": g})
    assert r.status_code == 403, r.text
    assert auth_client.get(f"/v1/share-views/{vid}").json()["members"] == []


@pytest.mark.public_profiles_off
def test_renaming_a_view_still_works_with_the_surface_off(
    auth_client: httpx.Client,
):
    """Renaming exposes nothing, so it is not part of the block - the gate is on
    loosening, not on touching a view at all."""
    vid, _ = _dormant_setup(auth_client)

    r = auth_client.patch(
        f"/v1/share-views/{vid}", json={"name": f"Renamed-{uuid.uuid4().hex[:6]}"}
    )
    assert r.status_code == 200, r.text


@pytest.mark.public_profiles_off
def test_removing_from_a_view_still_works_with_the_surface_off(
    auth_client: httpx.Client,
):
    """Taking somebody out of a dormant view is precisely how an owner stops
    them waking up with it, so no setting the owner does not control may stand
    in the way."""
    vid, _ = _dormant_setup(auth_client)
    m = _member(auth_client, "Curated", privacy="public")
    g = _group(auth_client, f"G-{uuid.uuid4().hex[:6]}")
    f = _field(auth_client, f"F-{uuid.uuid4().hex[:6]}")
    _stock_view_in_db(vid, member_id=m, field_id=f, group_id=g)

    assert (
        auth_client.delete(f"/v1/share-views/{vid}/members/{m}").status_code == 204
    )
    assert (
        auth_client.delete(f"/v1/share-views/{vid}/fields/{f}").status_code == 204
    )
    assert (
        auth_client.delete(f"/v1/share-views/{vid}/groups/{g}").status_code == 204
    )

    got = auth_client.get(f"/v1/share-views/{vid}").json()
    assert got["members"] == []
    assert got["fields"] == []
    assert got["groups"] == []


@pytest.mark.public_profiles_off
def test_loosening_is_refused_but_tightening_is_not(auth_client: httpx.Client):
    """The same asymmetry the rest of the feature runs on, applied to the
    operator's switch: a flag turned on now would come into force under
    whoever is around when the surface comes back."""
    vid, _ = _dormant_setup(auth_client)

    loosen = auth_client.patch(
        f"/v1/share-views/{vid}", json={"include_bio": True}
    )
    assert loosen.status_code == 403, loosen.text
    assert auth_client.get(f"/v1/share-views/{vid}").json()["include_bio"] is False

    tighten = auth_client.patch(
        f"/v1/share-views/{vid}", json={"include_members": False}
    )
    assert tighten.status_code == 200, tighten.text
    assert (
        auth_client.get(f"/v1/share-views/{vid}").json()["include_members"] is False
    )


@pytest.mark.public_profiles_off
def test_member_permalinks_cannot_be_turned_on_while_the_surface_is_off(
    auth_client: httpx.Client,
):
    """Permalinks sit outside the staging machinery, not outside this gate.

    They stage nothing and step up for nobody, because they publish no member
    the roster does not already publish - but turning them on mints an address
    per member, and those addresses start resolving the day an operator flips
    the switch back, with nobody around who agreed to them. Turning them off is
    a tightening and stays open, like every other way out on this router.
    """
    vid, _ = _dormant_setup(auth_client)

    on = auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_permalinks": True}
    )
    assert on.status_code == 403, on.text
    assert (
        auth_client.get(f"/v1/share-views/{vid}").json()["member_permalinks"]
        is False
    )

    off = auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_permalinks": False}
    )
    assert off.status_code == 200, off.text
    assert (
        auth_client.get(f"/v1/share-views/{vid}").json()["member_permalinks"]
        is False
    )


@pytest.mark.public_profiles_off
def test_member_permalinks_can_be_turned_off_while_the_surface_is_off(
    auth_client: httpx.Client,
):
    """The un-exposing direction on a view that HAS them on, which is the case
    that matters: a view curated while the surface was up, whose owner now wants
    the per-member addresses gone before it ever comes back."""
    _go_public(auth_client)
    _attest(auth_client)
    system_id = auth_client.get("/v1/systems/me").json()["id"]
    vid = _view_row_in_db(
        system_id, f"Dormant-{uuid.uuid4().hex[:6]}", member_permalinks=True
    )

    off = auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_permalinks": False}
    )
    assert off.status_code == 200, off.text
    assert (
        auth_client.get(f"/v1/share-views/{vid}").json()["member_permalinks"]
        is False
    )


@pytest.mark.public_profiles_off
def test_revoking_a_dormant_grant_still_works(auth_client: httpx.Client):
    """The whole reason the audit and this endpoint stay ungated: a dormant
    grant is exactly the one somebody needs to be able to kill before an
    operator turns the surface back on."""
    _, gid = _dormant_setup(auth_client)

    assert auth_client.delete(f"/v1/share-grants/{gid}").status_code == 204
    row = next(
        g for g in auth_client.get("/v1/share-grants").json() if g["id"] == gid
    )
    assert row["status"] == "revoked"
    assert row["revoked_at"] is not None


@pytest.mark.public_profiles_off
def test_rotating_a_dormant_link_still_works(auth_client: httpx.Client):
    """Rotation kills the old token outright, so it is an un-exposing act even
    while nothing is being served - and the old token is the one that would
    still be in somebody's chat history when the surface returns."""
    _, gid = _dormant_setup(auth_client, subject_type="link")

    rotated = auth_client.post(f"/v1/share-grants/{gid}/rotate")
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["token"]


@pytest.mark.public_profiles_off
def test_the_audit_still_lists_dormant_grants(auth_client: httpx.Client):
    """An audit that vanished with the setting would leave the owner unable to
    see - and therefore unable to revoke - what is waiting to resume."""
    vid, gid = _dormant_setup(auth_client)

    audit = auth_client.get("/v1/sharing/audit")
    assert audit.status_code == 200, audit.text
    entry = next(
        e for e in audit.json()["entries"] if e["grant"]["id"] == gid
    )
    assert entry["view_id"] == vid


@pytest.mark.public_profiles_off
def test_deleting_a_view_with_the_surface_off_still_works(
    auth_client: httpx.Client,
):
    """Deleting a view cascades its grants away, which is the bluntest way out
    and must not be gated on a setting the owner does not control."""
    vid, gid = _dormant_setup(auth_client)

    assert auth_client.delete(f"/v1/share-views/{vid}").status_code == 204
    assert not [
        g for g in auth_client.get("/v1/share-grants").json() if g["id"] == gid
    ]


@pytest.mark.public_profiles_off
def test_a_row_staged_before_the_switch_still_promotes(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    """The finalize sweep is deliberately NOT gated on the instance switch.

    Every row it can find was staged while the surface was on (nothing can stage
    one now), so its window is one the owner consented to. Freezing it would
    move the promotion onto an operator's schedule instead of the owner's -
    the same wake-up hazard, relocated. Nothing promoted here is served while
    the surface is off; the anonymous router 404s regardless.
    """
    vid, _ = _dormant_setup(auth_client)
    m = _member(auth_client, "Staged", privacy="public")
    _stock_view_in_db(vid, member_id=m, member_pending=True)

    staged = auth_client.get(f"/v1/share-views/{vid}").json()["members"]
    assert [row["status"] for row in staged] == ["pending"]

    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "success"

    promoted = auth_client.get(f"/v1/share-views/{vid}").json()["members"]
    assert [row["status"] for row in promoted] == ["active"]
