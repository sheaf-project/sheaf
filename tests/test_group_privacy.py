"""End-to-end coverage for the per-group privacy ceiling.

Needs the docker stack. Same asymmetric rule the rest of the sharing feature
follows: publishing a group that would ACTUALLY be served is a loosening
(re-auth now, live level moves only after the grace window), while everything
else - lowering, a raise nothing points at - is instant and ungated.

Where this deliberately differs from a relationship edge: an edge names two
people at once, so raising one is only an exposure when both of its endpoints
are already published by one view. A group has no endpoints to protect. The
group itself is the payload - the name, description and colour its owner wrote
- and its published roster is an INTERSECTION with the members the view already
shows, assembled in the projection. So the gate here is simply "is any view set
to show groups actually being served", and there is no both-ends test to write.

All three ways a group can end up published live here: the PATCH raise, the
CREATE that skips straight to public (or "delete it and add it back" would be a
way round the slower door), and the importer, which has no deliberate act to
hang a gate on and so demotes instead.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from tests._import_runner_helpers import drive_import_runner, wait_for_terminal

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8001")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _member(c: httpx.Client, name: str, privacy: str = "public") -> str:
    r = c.post("/v1/members", json={"name": name, "privacy": privacy})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _group(c: httpx.Client, name: str | None = None, **kw) -> str:
    r = c.post(
        "/v1/groups", json={"name": name or f"Grp-{uuid.uuid4().hex[:6]}", **kw}
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


def _view(c: httpx.Client, *, members: list[str] | None = None, **kw) -> str:
    r = c.post(
        "/v1/share-views",
        json={"name": f"Grp-{uuid.uuid4().hex[:6]}", **kw},
    )
    assert r.status_code == 201, r.text
    vid = r.json()["id"]
    for m in members or []:
        added = c.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
        assert added.status_code == 200, added.text
    return vid


def _go_public(c: httpx.Client) -> None:
    """System privacy is the master ceiling over the public surface, so a system
    has to be public before it can publish anything at all."""
    r = c.patch("/v1/systems/me", json={"privacy": "public"})
    assert r.status_code == 200, r.text


def _publish(c: httpx.Client, view_id: str) -> str:
    """Point a live public grant at a view. Called before safety is armed, so
    the grant itself is active rather than pending."""
    _go_public(c)
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


def _backdate_group_raise(group_id: str) -> None:
    """Make a staged group raise due for the finalize job."""

    async def _work(db) -> None:
        from sheaf.models.group import Group

        group = await db.get(Group, uuid.UUID(group_id))
        assert group is not None
        assert group.privacy_activates_at is not None
        group.privacy_activates_at = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)


def _raise(c: httpx.Client, group_id: str, **kw) -> httpx.Response:
    return c.patch(f"/v1/groups/{group_id}", json={"privacy": "public", **kw})


def _serving_view(c: httpx.Client) -> str:
    """A published view set to show groups: the setup where raising a group to
    public really would put it in front of somebody."""
    m = _member(c, f"GrpMember-{uuid.uuid4().hex[:6]}")
    vid = _view(c, members=[m], include_groups=True)
    _publish(c, vid)
    return vid


# ---------------------------------------------------------------------------
# The raise, gate by gate
# ---------------------------------------------------------------------------


def test_raise_is_staged_and_reauthed_when_a_view_serves_groups(
    auth_client: httpx.Client,
):
    _serving_view(auth_client)
    gid = _group(auth_client)
    _arm_visibility_safety(auth_client)

    denied = _raise(auth_client, gid)
    assert denied.status_code in (400, 403), denied.text

    ok = _raise(auth_client, gid, password="testpassword123")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    # Accepted, but staged: the live level has not moved.
    assert body["privacy"] == "private"
    assert body["pending_privacy"] == "public"
    assert body["privacy_activates_at"] is not None


def test_raise_is_instant_when_no_view_serves_groups(auth_client: httpx.Client):
    """A published view with the groups flag OFF serves no group, so there is
    nothing to delay."""
    m = _member(auth_client, "NoFlagMember")
    vid = _view(auth_client, members=[m], include_groups=False)
    _publish(auth_client, vid)
    gid = _group(auth_client)
    _arm_visibility_safety(auth_client)

    ok = _raise(auth_client, gid)
    assert ok.status_code == 200, ok.text
    assert ok.json()["privacy"] == "public"
    assert ok.json()["pending_privacy"] is None


def test_raise_is_instant_without_a_grant(auth_client: httpx.Client):
    """A curated but unpublished view points at nobody."""
    m = _member(auth_client, "NoGrantMember")
    _view(auth_client, members=[m], include_groups=True)
    gid = _group(auth_client)
    _arm_visibility_safety(auth_client)

    ok = _raise(auth_client, gid)
    assert ok.status_code == 200, ok.text
    assert ok.json()["privacy"] == "public"


def test_raise_is_instant_without_the_safety_category_armed(
    auth_client: httpx.Client,
):
    _serving_view(auth_client)
    gid = _group(auth_client)

    ok = _raise(auth_client, gid)
    assert ok.status_code == 200, ok.text
    assert ok.json()["privacy"] == "public"
    assert ok.json()["privacy_activates_at"] is None


def test_a_pending_groups_flag_still_counts(auth_client: httpx.Client):
    """Pending counts on every axis: a flag flip still inside its own grace
    window goes live on its own, so a raise requested now must serve its own
    full window rather than inheriting the remainder of that one."""
    m = _member(auth_client, "PendingFlagMember")
    vid = _view(auth_client, members=[m], include_groups=False)
    _publish(auth_client, vid)
    _arm_visibility_safety(auth_client)
    staged = auth_client.patch(
        f"/v1/share-views/{vid}",
        json={"include_groups": True, "password": "testpassword123"},
    )
    assert staged.status_code == 200, staged.text
    assert staged.json()["pending_include_groups"] is True

    gid = _group(auth_client)
    assert _raise(auth_client, gid).status_code in (400, 403)
    ok = _raise(auth_client, gid, password="testpassword123")
    assert ok.status_code == 200, ok.text
    assert ok.json()["pending_privacy"] == "public"


def test_a_pending_grant_still_counts(auth_client: httpx.Client):
    """Same reasoning from the other end: a grant inside its grace window goes
    live by itself, so it is a live exposure path for this purpose."""
    m = _member(auth_client, "PendingGrantMember")
    vid = _view(auth_client, members=[m], include_groups=True)
    _go_public(auth_client)
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

    gid = _group(auth_client)
    assert _raise(auth_client, gid).status_code in (400, 403)


# ---------------------------------------------------------------------------
# Going the other way is always instant
# ---------------------------------------------------------------------------


def test_lowering_is_instant_and_cancels_a_staged_raise(
    auth_client: httpx.Client,
):
    """Going dark always wins, and it wins at its own gate: the staged raise is
    dropped outright rather than queued behind the lowering."""
    _serving_view(auth_client)
    gid = _group(auth_client)
    _arm_visibility_safety(auth_client)
    staged = _raise(auth_client, gid, password="testpassword123")
    assert staged.json()["pending_privacy"] == "public"

    lowered = auth_client.patch(f"/v1/groups/{gid}", json={"privacy": "friends"})
    assert lowered.status_code == 200, lowered.text
    body = lowered.json()
    assert body["privacy"] == "friends"
    assert body["pending_privacy"] is None
    assert body["privacy_activates_at"] is None


def test_unpublishing_a_public_group_is_instant(auth_client: httpx.Client):
    _serving_view(auth_client)
    gid = _group(auth_client)
    published = _raise(auth_client, gid)
    assert published.json()["privacy"] == "public"
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(f"/v1/groups/{gid}", json={"privacy": "private"})
    assert r.status_code == 200, r.text
    assert r.json()["privacy"] == "private"


def test_renaming_a_group_never_needs_step_up(auth_client: httpx.Client):
    """The gate is on the privacy field, not on the endpoint: editing anything
    else about a group exposes nothing and must not demand a password."""
    _serving_view(auth_client)
    gid = _group(auth_client)
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(
        f"/v1/groups/{gid}", json={"name": "Renamed", "color": "#abcdef"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Renamed"
    assert r.json()["privacy"] == "private"


# ---------------------------------------------------------------------------
# Creating one straight to public
# ---------------------------------------------------------------------------


def test_create_public_runs_the_same_gate(auth_client: httpx.Client):
    """Otherwise "delete it and add it back as public" walks around the PATCH
    gate entirely."""
    _serving_view(auth_client)
    _arm_visibility_safety(auth_client)

    denied = auth_client.post(
        "/v1/groups", json={"name": "BornPublic", "privacy": "public"}
    )
    assert denied.status_code in (400, 403), denied.text

    ok = auth_client.post(
        "/v1/groups",
        json={
            "name": "BornPublic",
            "privacy": "public",
            "password": "testpassword123",
        },
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    # Born private with the raise staged, exactly like the PATCH path.
    assert body["privacy"] == "private"
    assert body["pending_privacy"] == "public"


def test_create_public_is_instant_when_nothing_serves_groups(
    auth_client: httpx.Client,
):
    _arm_visibility_safety(auth_client)
    r = auth_client.post(
        "/v1/groups", json={"name": "FreelyPublic", "privacy": "public"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["privacy"] == "public"


def test_a_group_is_born_private_by_default(auth_client: httpx.Client):
    r = auth_client.post("/v1/groups", json={"name": "DefaultLevel"})
    assert r.status_code == 201, r.text
    assert r.json()["privacy"] == "private"


# ---------------------------------------------------------------------------
# The finalize sweep
# ---------------------------------------------------------------------------


def test_finalize_promotes_a_staged_group_raise(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    _serving_view(auth_client)
    gid = _group(auth_client)
    _arm_visibility_safety(auth_client)
    staged = _raise(auth_client, gid, password="testpassword123")
    assert staged.json()["pending_privacy"] == "public"

    _backdate_group_raise(gid)
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text

    got = auth_client.get(f"/v1/groups/{gid}").json()
    assert got["privacy"] == "public"
    assert got["pending_privacy"] is None
    assert got["privacy_activates_at"] is None


def test_finalize_leaves_a_cancelled_raise_alone(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    """The cancellation cleared the timestamp, so the sweep's predicate does
    not match it and the group stays where the owner put it."""
    _serving_view(auth_client)
    gid = _group(auth_client)
    _arm_visibility_safety(auth_client)
    _raise(auth_client, gid, password="testpassword123")
    auth_client.patch(f"/v1/groups/{gid}", json={"privacy": "private"})

    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text
    assert auth_client.get(f"/v1/groups/{gid}").json()["privacy"] == "private"


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def _second_client() -> httpx.Client:
    c = httpx.Client(base_url=BASE_URL)
    email = f"grppriv-{uuid.uuid4().hex[:8]}@sheaf.dev"
    r = c.post("/v1/auth/register", json={"email": email, "password": "testpassword123"})
    assert r.status_code == 201
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


def test_cannot_raise_another_systems_group(auth_client: httpx.Client):
    other = _second_client()
    try:
        gid = _group(other)
        r = _raise(auth_client, gid)
        # Same 404 as a group that never existed: no cross-tenant oracle.
        assert r.status_code == 404, r.text
        assert other.get(f"/v1/groups/{gid}").json()["privacy"] == "private"
    finally:
        other.close()


# ---------------------------------------------------------------------------
# The importer: no deliberate act to gate, so it demotes instead
# ---------------------------------------------------------------------------


def _import(c: httpx.Client, payload: dict) -> dict:
    """Post a native export, drain the runner, return the finished job."""
    resp = c.post(
        "/v1/imports/file",
        files={
            "file": ("sheaf.json", json.dumps(payload).encode(), "application/json")
        },
        data={"source": "sheaf_file", "idempotency_key": str(uuid.uuid4())},
    )
    assert resp.status_code == 202, resp.text
    drive_import_runner()
    final = wait_for_terminal(c, resp.json()["id"])
    assert final["status"] == "complete", final
    return final


def _messages(job: dict) -> str:
    return " ".join(e["message"] for e in job["events"])


def _public_group_payload(name: str) -> dict:
    return {
        "version": "2",
        "system": {"name": "Imported Group System"},
        "members": [],
        "groups": [
            {"id": "g1", "name": name, "privacy": "public", "member_ids": []}
        ],
        "fronts": [],
        "tags": [],
        "custom_fields": [],
    }


def _imported_group_level(c: httpx.Client, name: str) -> str:
    dump = c.get("/v1/export").json()
    match = [g for g in dump["groups"] if g["name"] == name]
    assert len(match) == 1, dump["groups"]
    return match[0]["privacy"]


def test_import_demotes_a_public_group_when_a_view_serves_groups(
    auth_client: httpx.Client,
):
    """Restoring a backup must not publish a group. The owner-side raise has
    step-up and a grace window in front of it; an import has neither, so the
    level is dropped to private and the user is told."""
    _serving_view(auth_client)
    name = f"ImpHeld-{uuid.uuid4().hex[:6]}"

    job = _import(auth_client, _public_group_payload(name))
    assert "were marked public in the file" in _messages(job), job["events"]
    assert _imported_group_level(auth_client, name) == "private"


def test_import_keeps_a_public_group_when_no_view_serves_groups(
    auth_client: httpx.Client,
):
    """The important half: the guard must not flatten every import. With no
    view showing groups, nothing would have been served, so the file's level
    stands."""
    m = _member(auth_client, "ImpNoFlagMember")
    vid = _view(auth_client, members=[m], include_groups=False)
    _publish(auth_client, vid)
    name = f"ImpKept-{uuid.uuid4().hex[:6]}"

    job = _import(auth_client, _public_group_payload(name))
    assert "were marked public in the file" not in _messages(job)
    assert _imported_group_level(auth_client, name) == "public"


def test_import_defaults_a_missing_or_garbled_level_to_private(
    auth_client: httpx.Client,
):
    """The failure mode of a garbled file must be "too private", never
    "published"."""
    name = f"ImpGarbled-{uuid.uuid4().hex[:6]}"
    payload = _public_group_payload(name)
    payload["groups"][0]["privacy"] = "EVERYONE"

    _import(auth_client, payload)
    assert _imported_group_level(auth_client, name) == "private"


def test_import_does_not_republish_an_existing_group(auth_client: httpx.Client):
    """A name match is skipped wholesale rather than merged, so a file can
    neither publish an existing group nor un-publish one."""
    _serving_view(auth_client)
    name = f"ImpExisting-{uuid.uuid4().hex[:6]}"
    _group(auth_client, name)

    _import(auth_client, _public_group_payload(name))
    assert _imported_group_level(auth_client, name) == "private"
