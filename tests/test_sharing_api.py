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


def _backdate_grant_activation(grant_id: str) -> None:
    """Push a pending grant's activation into the past so the finalize job fires."""

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.share import ShareGrant

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            grant = await db.get(ShareGrant, uuid.UUID(grant_id))
            assert grant is not None
            grant.activates_at = datetime.now(UTC) - timedelta(minutes=1)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


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


# ---------------------------------------------------------------------------
# Audit surface
# ---------------------------------------------------------------------------


def test_audit_lists_live_grants_only(auth_client: httpx.Client):
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
