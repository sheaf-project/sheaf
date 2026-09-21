"""A member's privacy raise stages on the member, the way the other three do.

System, Group and CustomFieldDefinition all park a raise to public in
`pending_privacy` / `privacy_activates_at` while the live level stays put, and
the share finalizer promotes it. Member used to be the odd one out: the raise
flipped `privacy` at once and staged the EXPOSURE by demoting the member's
share-view rows instead. Same visible result for a curated roster, but the
member's own level said "public" days before anyone could see them, and any
roster built from the live ceiling rather than from rows would have shown
them immediately - a grace-period bypass in the exposure surface.

What these pin, gate by gate:

* the live level does not move while the raise is staged, and the anonymous
  roster keeps hiding the member by that level - no row demotion involved;
* nothing serves the member -> nothing to wait for, the raise is instant;
* lowering cancels a staged raise outright and never waits;
* the finalize sweep promotes it, and only when the timestamp is due;
* the owner's exposure banner reports it under the same `member_privacy` kind
  it always used for a member on their way onto a shared page.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8001")

# The anonymous roster is what proves the member stays hidden, so this runs
# where the public surface is on.
pytestmark = pytest.mark.public_profiles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _member(c: httpx.Client, name: str | None = None, privacy: str = "private") -> str:
    r = c.post(
        "/v1/members",
        json={"name": name or f"Stage-{uuid.uuid4().hex[:6]}", "privacy": privacy},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _arm_visibility_safety(c: httpx.Client) -> None:
    r = c.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_profile_visibility": True,
            "auth_tier": "password",
        },
    )
    assert r.status_code == 200, r.text


def _view(c: httpx.Client, *, members: list[str]) -> str:
    r = c.post("/v1/share-views", json={"name": f"View-{uuid.uuid4().hex[:6]}"})
    assert r.status_code == 201, r.text
    vid = r.json()["id"]
    for m in members:
        added = c.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
        assert added.status_code == 200, added.text
    return vid


def _publish(c: httpx.Client, view_id: str) -> str:
    """A live public grant on the view, minted before safety is armed so the
    grant itself is active. Returns the system id the anonymous URL needs."""
    r = c.patch("/v1/systems/me", json={"privacy": "public"})
    assert r.status_code == 200, r.text
    assert c.post("/v1/auth/me/attest-adult").status_code == 200
    granted = c.post(
        "/v1/share-grants", json={"view_id": view_id, "subject_type": "public"}
    )
    assert granted.status_code == 201, granted.text
    return c.get("/v1/systems/me").json()["id"]


def _raise(c: httpx.Client, member_id: str, **kw) -> httpx.Response:
    return c.patch(f"/v1/members/{member_id}", json={"privacy": "public", **kw})


def _public_roster_names(system_id: str) -> set[str]:
    with httpx.Client(base_url=BASE_URL) as anon:
        r = anon.get(f"/v1/public/systems/{system_id}/members")
    assert r.status_code == 200, r.text
    return {m["name"] for m in r.json()}


def _in_db(work) -> None:
    """Run `work(db)` straight against the test database, then commit. For the
    one thing the API deliberately refuses to do on request: backdate an
    activation timestamp."""

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            await work(db)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _backdate_member_raise(member_id: str) -> None:
    async def _work(db) -> None:
        from sheaf.models.member import Member

        member = await db.get(Member, uuid.UUID(member_id))
        assert member is not None
        assert member.privacy_activates_at is not None
        member.privacy_activates_at = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)


def _served_private_member(c: httpx.Client) -> tuple[str, str, str]:
    """A private member sitting in a published view: the one setup where a
    raise really would put them in front of somebody. Returns
    (member_id, member_name, system_id)."""
    name = f"Stage-{uuid.uuid4().hex[:6]}"
    mid = _member(c, name)
    vid = _view(c, members=[mid])
    sid = _publish(c, vid)
    return mid, name, sid


# ---------------------------------------------------------------------------
# The raise, gate by gate
# ---------------------------------------------------------------------------


def test_raise_is_staged_on_the_member_and_the_roster_keeps_hiding_them(
    auth_client: httpx.Client,
):
    mid, name, sid = _served_private_member(auth_client)
    _arm_visibility_safety(auth_client)
    assert name not in _public_roster_names(sid)

    denied = _raise(auth_client, mid)
    assert denied.status_code in (400, 403), denied.text

    ok = _raise(auth_client, mid, password="testpassword123")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    # Accepted, but staged: the live level has not moved, the pair says what
    # it will become and when.
    assert body["privacy"] == "private"
    assert body["pending_privacy"] == "public"
    assert body["privacy_activates_at"] is not None

    # And that live level is what the anonymous roster filters on, so the
    # member is still not there - without a single membership row having been
    # touched. This is the property a live roster needs.
    assert name not in _public_roster_names(sid)
    got = auth_client.get(f"/v1/members/{mid}").json()
    assert got["privacy"] == "private" and got["pending_privacy"] == "public"


def test_raise_is_instant_when_nothing_serves_the_member(auth_client: httpx.Client):
    """A member in no published view exposes nothing when raised, so there is
    nothing to wait for and nothing to stage."""
    mid = _member(auth_client)
    _arm_visibility_safety(auth_client)

    ok = _raise(auth_client, mid)
    assert ok.status_code == 200, ok.text
    assert ok.json()["privacy"] == "public"
    assert ok.json()["pending_privacy"] is None
    assert ok.json()["privacy_activates_at"] is None


def test_lowering_is_instant_and_cancels_a_staged_raise(auth_client: httpx.Client):
    mid, _, _ = _served_private_member(auth_client)
    _arm_visibility_safety(auth_client)
    staged = _raise(auth_client, mid, password="testpassword123")
    assert staged.json()["pending_privacy"] == "public"

    # No password: going dark is never gated, and it takes the staged raise
    # with it.
    lowered = auth_client.patch(f"/v1/members/{mid}", json={"privacy": "private"})
    assert lowered.status_code == 200, lowered.text
    assert lowered.json()["privacy"] == "private"
    assert lowered.json()["pending_privacy"] is None
    assert lowered.json()["privacy_activates_at"] is None


def test_finalize_promotes_a_staged_member_raise(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    mid, name, sid = _served_private_member(auth_client)
    _arm_visibility_safety(auth_client)
    staged = _raise(auth_client, mid, password="testpassword123")
    assert staged.json()["pending_privacy"] == "public"

    # Not due yet: the sweep leaves it alone.
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text
    assert auth_client.get(f"/v1/members/{mid}").json()["privacy"] == "private"
    assert name not in _public_roster_names(sid)

    _backdate_member_raise(mid)
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text

    got = auth_client.get(f"/v1/members/{mid}").json()
    assert got["privacy"] == "public"
    assert got["pending_privacy"] is None
    assert got["privacy_activates_at"] is None
    # The window elapsed and the ceiling moved; the untouched membership row
    # serves them now.
    assert name in _public_roster_names(sid)


def test_finalize_leaves_a_cancelled_raise_alone(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    """The cancellation cleared the timestamp, so the sweep's predicate does
    not match it and the member stays where the owner put them."""
    mid, name, sid = _served_private_member(auth_client)
    _arm_visibility_safety(auth_client)
    _raise(auth_client, mid, password="testpassword123")
    auth_client.patch(f"/v1/members/{mid}", json={"privacy": "private"})

    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text
    assert auth_client.get(f"/v1/members/{mid}").json()["privacy"] == "private"
    assert name not in _public_roster_names(sid)


def test_the_exposure_banner_reports_the_staged_raise(auth_client: httpx.Client):
    """Same `member_privacy` kind the banner always used for a member on their
    way onto a shared page, now read off the member's own column."""
    mid, _, _ = _served_private_member(auth_client)
    _arm_visibility_safety(auth_client)
    staged = _raise(auth_client, mid, password="testpassword123")
    activates_at = staged.json()["privacy_activates_at"]

    listing = auth_client.get("/v1/system/safety").json()["pending_exposures"]
    kinds = {e["kind"]: e["activates_at"] for e in listing}
    assert "member_privacy" in kinds, listing
    assert kinds["member_privacy"] == activates_at
