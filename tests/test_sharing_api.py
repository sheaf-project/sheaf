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
    vid = _shared_view(auth_client)
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
    groups, and none at all while the flag is off."""
    _go_public(auth_client)
    _attest(auth_client)
    vid = _view(auth_client, "GroupAudit")
    shown = _group(auth_client, "Shown")
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


def test_audit_reports_the_member_list_and_permalink_settings(
    auth_client: httpx.Client,
):
    """With the roster off, `member_count` still reports the curation (nothing
    was destroyed) and `include_members` is what says it is not being served."""
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
    assert entry["member_permalinks"] is True


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
