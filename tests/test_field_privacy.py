"""End-to-end coverage for the custom-field definition privacy ceiling.

Needs the docker stack. `custom_field_definitions.privacy` has existed since
the pre-views design and until now enforced nothing: a field appeared on a
shared view because the owner had selected it into that view, whatever the
level beside it said. These tests pin both halves of lighting it up - the
projection now refuses a non-public definition, and raising one to public goes
through the same door every other ceiling does.

Same asymmetric rule as the rest of the sharing feature: publishing a field
that would ACTUALLY be served is a loosening (re-auth now, live level moves
only after the grace window), while everything else - lowering, a raise nothing
points at - is instant and ungated.

Two gates decide whether a raise is an exposure, and they are the interesting
part: some view must have this definition SELECTED (a field is not published
system-wide the way a group is), and that view must have its roster on, because
field values render inside member cards and nowhere else.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8001")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _member(c: httpx.Client, name: str, privacy: str = "public") -> str:
    r = c.post("/v1/members", json={"name": name, "privacy": privacy})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _field(c: httpx.Client, name: str | None = None, **kw) -> str:
    r = c.post(
        "/v1/fields",
        json={
            "name": name or f"Fld-{uuid.uuid4().hex[:6]}",
            "field_type": "text",
            **kw,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


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


def _view(
    c: httpx.Client,
    *,
    members: list[str] | None = None,
    fields: list[str] | None = None,
    **kw,
) -> str:
    r = c.post("/v1/share-views", json={"name": f"Fld-{uuid.uuid4().hex[:6]}", **kw})
    assert r.status_code == 201, r.text
    vid = r.json()["id"]
    for m in members or []:
        added = c.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
        assert added.status_code == 200, added.text
    for f in fields or []:
        added = c.post(f"/v1/share-views/{vid}/fields", json={"field_id": f})
        assert added.status_code == 200, added.text
    return vid


def _publish(c: httpx.Client, view_id: str) -> str:
    """Point a live public grant at a view. Called before safety is armed, so
    the grant itself is active rather than pending."""
    r = c.post("/v1/auth/me/attest-adult")
    assert r.status_code == 200, r.text
    granted = c.post(
        "/v1/share-grants", json={"view_id": view_id, "subject_type": "public"}
    )
    assert granted.status_code == 201, granted.text
    return granted.json()["grant"]["id"]


def _in_db(work) -> None:
    """Run `work(db)` straight against the test database, then commit.

    For the one thing the API deliberately refuses to do on request: backdate
    an activation timestamp.
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


def _backdate_field_raise(field_id: str) -> None:
    """Make a staged field raise due for the finalize job."""

    async def _work(db) -> None:
        from datetime import UTC, datetime, timedelta

        from sheaf.models.custom_field import CustomFieldDefinition

        field = await db.get(CustomFieldDefinition, uuid.UUID(field_id))
        assert field is not None
        assert field.privacy_activates_at is not None
        field.privacy_activates_at = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)


def _raise(c: httpx.Client, field_id: str, **kw) -> httpx.Response:
    return c.patch(f"/v1/fields/{field_id}", json={"privacy": "public", **kw})


def _serving_view(c: httpx.Client, field_id: str) -> str:
    """A published view with a roster that has this field selected: the setup
    where raising the definition to public really would put it in front of
    somebody."""
    m = _member(c, f"FldMember-{uuid.uuid4().hex[:6]}")
    vid = _view(c, members=[m], fields=[field_id], include_members=True)
    _publish(c, vid)
    return vid


def _audit_entry(c: httpx.Client, view_id: str) -> dict:
    audit = c.get("/v1/sharing/audit")
    assert audit.status_code == 200, audit.text
    entries = [e for e in audit.json()["entries"] if e["view_id"] == view_id]
    assert len(entries) == 1, audit.text
    return entries[0]


# ---------------------------------------------------------------------------
# The raise, gate by gate
# ---------------------------------------------------------------------------


def test_raise_is_staged_and_reauthed_when_a_view_serves_the_field(
    auth_client: httpx.Client,
):
    fid = _field(auth_client)
    _serving_view(auth_client, fid)
    _arm_visibility_safety(auth_client)

    denied = _raise(auth_client, fid)
    assert denied.status_code in (400, 403), denied.text

    ok = _raise(auth_client, fid, password="testpassword123")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    # Accepted, but staged: the live level has not moved.
    assert body["privacy"] == "private"
    assert body["pending_privacy"] == "public"
    assert body["privacy_activates_at"] is not None


def test_raise_is_instant_when_the_field_is_in_no_view(auth_client: httpx.Client):
    """Selection is per-definition, so another field being served says nothing
    about this one. The gate has to ask about the field in hand."""
    served = _field(auth_client)
    _serving_view(auth_client, served)
    unselected = _field(auth_client)
    _arm_visibility_safety(auth_client)

    ok = _raise(auth_client, unselected)
    assert ok.status_code == 200, ok.text
    assert ok.json()["privacy"] == "public"
    assert ok.json()["pending_privacy"] is None


def test_raise_is_instant_when_the_roster_is_off(auth_client: httpx.Client):
    """Field values render inside member cards and nowhere else, so a view with
    no roster serves no field however carefully it was selected."""
    m = _member(auth_client, "NoRosterMember")
    fid = _field(auth_client)
    vid = _view(auth_client, members=[m], fields=[fid], include_members=False)
    _publish(auth_client, vid)
    _arm_visibility_safety(auth_client)

    ok = _raise(auth_client, fid)
    assert ok.status_code == 200, ok.text
    assert ok.json()["privacy"] == "public"


def test_raise_is_instant_without_a_grant(auth_client: httpx.Client):
    """A curated but unpublished view points at nobody."""
    m = _member(auth_client, "NoGrantMember")
    fid = _field(auth_client)
    _view(auth_client, members=[m], fields=[fid])
    _arm_visibility_safety(auth_client)

    ok = _raise(auth_client, fid)
    assert ok.status_code == 200, ok.text
    assert ok.json()["privacy"] == "public"


def test_raise_is_instant_without_the_safety_category_armed(
    auth_client: httpx.Client,
):
    fid = _field(auth_client)
    _serving_view(auth_client, fid)

    ok = _raise(auth_client, fid)
    assert ok.status_code == 200, ok.text
    assert ok.json()["privacy"] == "public"
    assert ok.json()["privacy_activates_at"] is None


def test_a_pending_selection_still_counts(auth_client: httpx.Client):
    """Pending counts on every axis: a field row still inside its own grace
    window goes live on its own, so a raise requested now must serve its own
    full window rather than inheriting the remainder of that one."""
    m = _member(auth_client, "PendingSelectionMember")
    vid = _view(auth_client, members=[m])
    _publish(auth_client, vid)
    _arm_visibility_safety(auth_client)

    fid = _field(auth_client)
    added = auth_client.post(
        f"/v1/share-views/{vid}/fields",
        json={"field_id": fid, "password": "testpassword123"},
    )
    assert added.status_code == 200, added.text
    assert added.json()["fields"][0]["status"] == "pending"

    assert _raise(auth_client, fid).status_code in (400, 403)
    ok = _raise(auth_client, fid, password="testpassword123")
    assert ok.status_code == 200, ok.text
    assert ok.json()["pending_privacy"] == "public"


def test_a_pending_roster_flag_still_counts(auth_client: httpx.Client):
    """Same reasoning from the flag end: a staged `include_members` goes live by
    itself, so it is a live exposure path for this purpose."""
    m = _member(auth_client, "PendingRosterMember")
    fid = _field(auth_client)
    vid = _view(auth_client, members=[m], fields=[fid], include_members=False)
    _publish(auth_client, vid)
    _arm_visibility_safety(auth_client)
    staged = auth_client.patch(
        f"/v1/share-views/{vid}",
        json={"include_members": True, "password": "testpassword123"},
    )
    assert staged.status_code == 200, staged.text
    assert staged.json()["pending_include_members"] is True

    assert _raise(auth_client, fid).status_code in (400, 403)


def test_a_pending_grant_still_counts(auth_client: httpx.Client):
    """And from the grant end: a grant inside its grace window goes live by
    itself too."""
    m = _member(auth_client, "PendingGrantMember")
    fid = _field(auth_client)
    vid = _view(auth_client, members=[m], fields=[fid])
    _arm_visibility_safety(auth_client)
    auth_client.post("/v1/auth/me/attest-adult")
    granted = auth_client.post(
        "/v1/share-grants",
        json={
            "view_id": vid,
            "subject_type": "public",
            "password": "testpassword123",
        },
    )
    assert granted.status_code == 201, granted.text
    assert granted.json()["grant"]["status"] == "pending"

    assert _raise(auth_client, fid).status_code in (400, 403)


# ---------------------------------------------------------------------------
# Going the other way is always instant
# ---------------------------------------------------------------------------


def test_lowering_is_instant_and_cancels_a_staged_raise(auth_client: httpx.Client):
    """Going dark always wins, and it wins at its own gate: the staged raise is
    dropped outright rather than queued behind the lowering."""
    fid = _field(auth_client)
    _serving_view(auth_client, fid)
    _arm_visibility_safety(auth_client)
    staged = _raise(auth_client, fid, password="testpassword123")
    assert staged.json()["pending_privacy"] == "public"

    lowered = auth_client.patch(f"/v1/fields/{fid}", json={"privacy": "friends"})
    assert lowered.status_code == 200, lowered.text
    body = lowered.json()
    assert body["privacy"] == "friends"
    assert body["pending_privacy"] is None
    assert body["privacy_activates_at"] is None


def test_unpublishing_a_public_field_is_instant(auth_client: httpx.Client):
    fid = _field(auth_client)
    _serving_view(auth_client, fid)
    published = _raise(auth_client, fid)
    assert published.json()["privacy"] == "public"
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(f"/v1/fields/{fid}", json={"privacy": "private"})
    assert r.status_code == 200, r.text
    assert r.json()["privacy"] == "private"


def test_renaming_a_field_never_needs_step_up(auth_client: httpx.Client):
    """The gate is on the privacy field, not on the endpoint: editing anything
    else about a definition exposes nothing and must not demand a password."""
    fid = _field(auth_client)
    _serving_view(auth_client, fid)
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(f"/v1/fields/{fid}", json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Renamed"
    assert r.json()["privacy"] == "private"


# ---------------------------------------------------------------------------
# Adding to a view is not the exposure
# ---------------------------------------------------------------------------


def test_a_private_field_can_still_be_added_to_a_view(auth_client: httpx.Client):
    """Selection and the ceiling are two independent gates, so staging the
    curation first is allowed - it publishes nothing on its own. Same as adding
    a private member to a view."""
    m = _member(auth_client, "CurationMember")
    fid = _field(auth_client)
    vid = _view(auth_client, members=[m])

    added = auth_client.post(
        f"/v1/share-views/{vid}/fields", json={"field_id": fid}
    )
    assert added.status_code == 200, added.text
    assert [f["field_id"] for f in added.json()["fields"]] == [fid]
    assert auth_client.get(f"/v1/fields/{fid}").json()["privacy"] == "private"


# ---------------------------------------------------------------------------
# Creating one straight to public
# ---------------------------------------------------------------------------


def test_create_public_runs_the_same_gate(auth_client: httpx.Client):
    """Otherwise "delete it and add it back as public" walks around the PATCH
    gate entirely."""
    served = _field(auth_client)
    _serving_view(auth_client, served)
    _arm_visibility_safety(auth_client)

    denied = auth_client.post(
        "/v1/fields",
        json={"name": "BornPublic", "field_type": "text", "privacy": "public"},
    )
    assert denied.status_code in (400, 403), denied.text

    ok = auth_client.post(
        "/v1/fields",
        json={
            "name": "BornPublic",
            "field_type": "text",
            "privacy": "public",
            "password": "testpassword123",
        },
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    # Born private with the raise staged, exactly like the PATCH path.
    assert body["privacy"] == "private"
    assert body["pending_privacy"] == "public"


def test_create_public_is_instant_when_nothing_is_served(auth_client: httpx.Client):
    _arm_visibility_safety(auth_client)
    r = auth_client.post(
        "/v1/fields",
        json={"name": "FreelyPublic", "field_type": "text", "privacy": "public"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["privacy"] == "public"


def test_a_field_is_born_private_by_default(auth_client: httpx.Client):
    r = auth_client.post(
        "/v1/fields", json={"name": "DefaultLevel", "field_type": "text"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["privacy"] == "private"
    assert r.json()["pending_privacy"] is None


# ---------------------------------------------------------------------------
# The finalize sweep
# ---------------------------------------------------------------------------


def test_finalize_promotes_a_staged_field_raise(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    fid = _field(auth_client)
    _serving_view(auth_client, fid)
    _arm_visibility_safety(auth_client)
    staged = _raise(auth_client, fid, password="testpassword123")
    assert staged.json()["pending_privacy"] == "public"

    _backdate_field_raise(fid)
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text

    got = auth_client.get(f"/v1/fields/{fid}").json()
    assert got["privacy"] == "public"
    assert got["pending_privacy"] is None
    assert got["privacy_activates_at"] is None


def test_finalize_leaves_a_cancelled_raise_alone(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    """The cancellation cleared the timestamp, so the sweep's predicate does
    not match it and the field stays where the owner put it."""
    fid = _field(auth_client)
    _serving_view(auth_client, fid)
    _arm_visibility_safety(auth_client)
    _raise(auth_client, fid, password="testpassword123")
    auth_client.patch(f"/v1/fields/{fid}", json={"privacy": "private"})

    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text
    assert auth_client.get(f"/v1/fields/{fid}").json()["privacy"] == "private"


# ---------------------------------------------------------------------------
# The audit counts what is served, not what is selected
# ---------------------------------------------------------------------------


def test_audit_field_count_applies_the_ceiling(auth_client: httpx.Client):
    """An audit that disagreed with what visitors actually get would be worse
    than no audit at all, so the count comes through the projection's own
    filter."""
    m = _member(auth_client, "AuditMember")
    shown = _field(auth_client, "AuditShown")
    hidden = _field(auth_client, "AuditHidden")
    vid = _view(auth_client, members=[m], fields=[shown, hidden])
    _publish(auth_client, vid)

    # Both selected, neither public yet.
    assert _audit_entry(auth_client, vid)["field_count"] == 0

    raised = _raise(auth_client, shown)
    assert raised.status_code == 200, raised.text
    assert _audit_entry(auth_client, vid)["field_count"] == 1


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def _second_client() -> httpx.Client:
    c = httpx.Client(base_url=BASE_URL)
    email = f"fldpriv-{uuid.uuid4().hex[:8]}@sheaf.dev"
    r = c.post("/v1/auth/register", json={"email": email, "password": "testpassword123"})
    assert r.status_code == 201
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


def test_cannot_raise_another_systems_field(auth_client: httpx.Client):
    other = _second_client()
    try:
        fid = _field(other)
        r = _raise(auth_client, fid)
        # Same 404 as a field that never existed: no cross-tenant oracle.
        assert r.status_code == 404, r.text
        assert other.get(f"/v1/fields/{fid}").json()["privacy"] == "private"
    finally:
        other.close()


# ---------------------------------------------------------------------------
# The projection: the half that actually protects anybody
# ---------------------------------------------------------------------------


@pytest.mark.public_profiles
def test_a_non_public_field_never_reaches_a_member_payload(
    auth_client: httpx.Client,
):
    """Selection alone used to be enough. It is not any more: the definition
    has to clear its own ceiling as well, and raising it is what makes the
    value appear."""
    m = _member(auth_client, "ProjectedMember")
    held = _field(auth_client, "HeldField")
    shown = _field(auth_client, "ShownField")
    r = auth_client.put(
        f"/v1/members/{m}/fields",
        json=[
            {"field_id": held, "value": "hush"},
            {"field_id": shown, "value": "Protector"},
        ],
    )
    assert r.status_code == 200, r.text

    vid = _view(auth_client, members=[m], fields=[held, shown])
    _publish(auth_client, vid)
    # Safety is not armed, so this raise lands immediately.
    assert _raise(auth_client, shown).json()["privacy"] == "public"

    system_id = auth_client.get("/v1/systems/me").json()["id"]
    with httpx.Client(base_url=BASE_URL) as anon:
        members = anon.get(f"/v1/public/systems/{system_id}/members").json()
    assert len(members) == 1, members
    assert members[0]["fields"] == {"ShownField": "Protector"}
    assert "HeldField" not in members[0]["fields"]


@pytest.mark.public_profiles
def test_lowering_a_field_takes_it_off_a_live_profile(auth_client: httpx.Client):
    """Un-exposing is instant everywhere, including here: the value is gone
    from the next request, with no window to wait out."""
    m = _member(auth_client, "LoweredMember")
    fid = _field(auth_client, "LoweredField")
    r = auth_client.put(
        f"/v1/members/{m}/fields", json=[{"field_id": fid, "value": "Protector"}]
    )
    assert r.status_code == 200, r.text
    vid = _view(auth_client, members=[m], fields=[fid])
    _publish(auth_client, vid)
    assert _raise(auth_client, fid).json()["privacy"] == "public"

    system_id = auth_client.get("/v1/systems/me").json()["id"]
    with httpx.Client(base_url=BASE_URL) as anon:
        before = anon.get(f"/v1/public/systems/{system_id}/members").json()
        assert before[0]["fields"] == {"LoweredField": "Protector"}

        _arm_visibility_safety(auth_client)
        lowered = auth_client.patch(f"/v1/fields/{fid}", json={"privacy": "private"})
        assert lowered.status_code == 200, lowered.text

        after = anon.get(f"/v1/public/systems/{system_id}/members").json()
    assert after[0]["fields"] == {}
