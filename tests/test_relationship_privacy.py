"""End-to-end coverage for per-edge relationship privacy.

Needs the docker stack. The rule under test is the asymmetric one the rest of
the sharing feature already follows: publishing an edge that would ACTUALLY be
drawn for somebody is a loosening (re-auth now, live level moves only after the
grace window), while everything else - lowering, a raise nothing points at, a
group edge - is instant and ungated.

Raising an edge only exposes it when the whole chain is already in place: both
endpoints public and shareable, both in the SAME view, that view including
relationships, and a live grant pointing at it. Each link gets its own test so
a regression names the link that broke.

All three ways an edge can end up published live here: the PATCH raise, the
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
    """Public by default: an edge only ever projects between two public
    members, so that is the interesting case here."""
    r = c.post("/v1/members", json={"name": name, "privacy": privacy})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _group(c: httpx.Client, name: str) -> str:
    r = c.post("/v1/groups", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _type(c: httpx.Client, name: str | None = None, **kw) -> str:
    body = {
        "name": name or f"Type-{uuid.uuid4().hex[:6]}",
        "symmetry": "symmetric",
        "forward_label": "partner",
        **kw,
    }
    r = c.post("/v1/relationship-types", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create(
    c: httpx.Client, source: str, target: str, type_id: str, **kw
) -> httpx.Response:
    return c.post(
        "/v1/member-relationships",
        json={
            "source_id": source,
            "target_id": target,
            "relationship_type_id": type_id,
            **kw,
        },
    )


def _edge(c: httpx.Client, source: str, target: str, type_id: str, **kw) -> str:
    r = _create(c, source, target, type_id, **kw)
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


def _disarm_visibility_safety(c: httpx.Client) -> None:
    """Turn the profile_visibility category off. It defaults ON now, so a test
    that wants the pre-arm 'nothing is gated' baseline has to say so."""
    r = c.patch(
        "/v1/system/safety",
        json={"applies_to_profile_visibility": False},
    )
    assert r.status_code == 200, r.text


def _view(c: httpx.Client, *, members: list[str], **kw) -> str:
    r = c.post(
        "/v1/share-views",
        json={"name": f"Rel-{uuid.uuid4().hex[:6]}", **kw},
    )
    assert r.status_code == 201, r.text
    vid = r.json()["id"]
    for m in members:
        added = c.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
        assert added.status_code == 200, added.text
    return vid


def _publish(c: httpx.Client, view_id: str) -> str:
    """Point a live public grant at a view. Called before safety is armed, so
    the grant itself is active rather than pending."""
    # System privacy is the master ceiling over the public surface, so a system
    # has to be public before it can publish anything at all.
    flipped = c.patch("/v1/systems/me", json={"privacy": "public"})
    assert flipped.status_code == 200, flipped.text
    r = c.post("/v1/auth/me/attest-adult")
    assert r.status_code == 200, r.text
    granted = c.post(
        "/v1/share-grants", json={"view_id": view_id, "subject_type": "public"}
    )
    assert granted.status_code == 201, granted.text
    return granted.json()["grant"]["id"]


def _second_client() -> httpx.Client:
    c = httpx.Client(base_url=BASE_URL)
    email = f"reledge-{uuid.uuid4().hex[:8]}@sheaf.dev"
    r = c.post("/v1/auth/register", json={"email": email, "password": "testpassword123"})
    assert r.status_code == 201
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


def _in_db(work) -> None:
    """Run `work(db)` straight against the test database, then commit.

    For the two things the API deliberately refuses to do on request: backdate
    an activation timestamp, and leave a never-shareable member sitting in a
    view (the API pulls them out, which would confuse that case with "endpoint
    not in the view").
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


def _backdate_edge_raise(edge_id: str) -> None:
    """Make a staged edge raise due for the finalize job."""

    async def _work(db) -> None:
        from sheaf.models.relationship import MemberRelationship

        edge = await db.get(MemberRelationship, uuid.UUID(edge_id))
        assert edge is not None
        assert edge.visibility_activates_at is not None
        edge.visibility_activates_at = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)


def _mark_never_shareable_in_db(member_id: str) -> None:
    """Set the flag without going through the API, which would also remove the
    member's view rows."""

    async def _work(db) -> None:
        from sheaf.models.member import Member

        member = await db.get(Member, uuid.UUID(member_id))
        assert member is not None
        member.never_shareable = True

    _in_db(_work)


def _raise(c: httpx.Client, edge_id: str, **kw) -> httpx.Response:
    return c.patch(
        f"/v1/member-relationships/{edge_id}", json={"visibility": "public", **kw}
    )


def _exposing_pair(c: httpx.Client) -> tuple[str, str, str]:
    """Two public members in one published, relationship-serving view: the
    setup where an edge between them really would be drawn.

    Returns (view_id, source_id, target_id) so a test can knock out exactly one
    link of the chain.
    """
    a = _member(c, f"RelA-{uuid.uuid4().hex[:6]}")
    b = _member(c, f"RelB-{uuid.uuid4().hex[:6]}")
    vid = _view(c, members=[a, b], include_relationships=True)
    _publish(c, vid)
    return vid, a, b


def _publishable_edge(c: httpx.Client) -> tuple[str, str, str, str]:
    """`_exposing_pair` plus a private edge between the two, ready to raise."""
    vid, a, b = _exposing_pair(c)
    return _edge(c, a, b, _type(c)), vid, a, b


# ---------------------------------------------------------------------------
# The raise that really does expose: staged and re-auth gated
# ---------------------------------------------------------------------------


def test_raise_to_public_is_deferred_and_reauthed(auth_client: httpx.Client):
    edge, _, a, _ = _publishable_edge(auth_client)
    _arm_visibility_safety(auth_client)

    denied = _raise(auth_client, edge)
    assert denied.status_code == 400, denied.text

    ok = _raise(auth_client, edge, password="testpassword123")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    # Accepted, but the live level has not moved.
    assert body["visibility"] == "private"
    assert body["pending_visibility"] == "public"
    assert body["visibility_activates_at"] is not None

    # The staged state is visible from the endpoint's own viewpoint too, so the
    # editor can say "publishing on <date>" rather than silently showing private.
    viewpoint = auth_client.get(f"/v1/members/{a}/relationships").json()
    row = next(e for e in viewpoint if e["id"] == edge)
    assert row["visibility"] == "private"
    assert row["pending_visibility"] == "public"


def test_staged_raise_serves_the_full_grace_window(auth_client: httpx.Client):
    edge, _, _, _ = _publishable_edge(auth_client)
    _arm_visibility_safety(auth_client)

    before = datetime.now(UTC)
    staged = _raise(auth_client, edge, password="testpassword123")
    assert staged.status_code == 200, staged.text
    activates_at = datetime.fromisoformat(staged.json()["visibility_activates_at"])
    assert activates_at > before + timedelta(days=6, hours=23)


# ---------------------------------------------------------------------------
# Raises that expose nothing: instant, ungated, one test per broken link
# ---------------------------------------------------------------------------


def test_raise_is_instant_when_the_system_is_not_safeguarded(auth_client: httpx.Client):
    """Everything is in place to expose the edge; the owner has turned the
    profile-visibility category off, so nothing gates the raise. The category is
    on by default now, so this disarms it explicitly."""
    edge, _, _, _ = _publishable_edge(auth_client)
    _disarm_visibility_safety(auth_client)

    r = _raise(auth_client, edge)
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "public"
    assert r.json()["pending_visibility"] is None
    assert r.json()["visibility_activates_at"] is None


def test_raise_is_instant_without_a_live_grant(auth_client: httpx.Client):
    """The view is curated and includes relationships, but nothing points at
    it, so publishing the edge puts it in front of nobody."""
    a = _member(auth_client, "NoGrantA")
    b = _member(auth_client, "NoGrantB")
    _view(auth_client, members=[a, b], include_relationships=True)
    edge = _edge(auth_client, a, b, _type(auth_client))
    _arm_visibility_safety(auth_client)

    r = _raise(auth_client, edge)
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "public"
    assert r.json()["pending_visibility"] is None


def test_raise_is_instant_when_one_endpoint_is_not_in_the_view(
    auth_client: httpx.Client,
):
    """An edge needs BOTH ends in the same view to be drawn."""
    a = _member(auth_client, "InViewA")
    b = _member(auth_client, "OutsideB")
    vid = _view(auth_client, members=[a], include_relationships=True)
    _publish(auth_client, vid)
    edge = _edge(auth_client, a, b, _type(auth_client))
    _arm_visibility_safety(auth_client)

    r = _raise(auth_client, edge)
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "public"
    assert r.json()["pending_visibility"] is None


def test_raise_is_instant_when_one_endpoint_is_not_public(auth_client: httpx.Client):
    """member.privacy is the ceiling the projection applies first: a private
    endpoint means the edge cannot be drawn whatever the edge says."""
    edge, _, _, b = _publishable_edge(auth_client)
    lowered = auth_client.patch(f"/v1/members/{b}", json={"privacy": "private"})
    assert lowered.status_code == 200, lowered.text
    _arm_visibility_safety(auth_client)

    r = _raise(auth_client, edge)
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "public"
    assert r.json()["pending_visibility"] is None


def test_raise_is_instant_when_one_endpoint_is_never_shareable(
    auth_client: httpx.Client,
):
    """Set straight in the database so the membership row survives: this is the
    never_shareable guard being tested, not "the member left the view"."""
    edge, _, _, b = _publishable_edge(auth_client)
    _mark_never_shareable_in_db(b)
    _arm_visibility_safety(auth_client)

    r = _raise(auth_client, edge)
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "public"
    assert r.json()["pending_visibility"] is None


def test_raise_is_instant_when_the_view_excludes_relationships(
    auth_client: httpx.Client,
):
    """The published view does not serve relationships at all, so a public edge
    inside it reaches nobody."""
    a = _member(auth_client, "NoRelFlagA")
    b = _member(auth_client, "NoRelFlagB")
    vid = _view(auth_client, members=[a, b], include_relationships=False)
    _publish(auth_client, vid)
    edge = _edge(auth_client, a, b, _type(auth_client))
    _arm_visibility_safety(auth_client)

    r = _raise(auth_client, edge)
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "public"
    assert r.json()["pending_visibility"] is None


# ---------------------------------------------------------------------------
# Creating an edge already public: the same gate, or the PATCH one is theatre
# ---------------------------------------------------------------------------


def test_create_public_edge_is_deferred_and_reauthed(auth_client: httpx.Client):
    _, a, b = _exposing_pair(auth_client)
    t = _type(auth_client)
    _arm_visibility_safety(auth_client)

    denied = _create(auth_client, a, b, t, visibility="public")
    # The step-up gate, not one of the create endpoint's own 400s.
    assert denied.status_code == 400, denied.text
    assert "password" in denied.text.lower(), denied.text

    ok = _create(
        auth_client, a, b, t, visibility="public", password="testpassword123"
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    # Born private with the raise staged behind it: created, not published.
    assert body["visibility"] == "private"
    assert body["pending_visibility"] == "public"
    assert body["visibility_activates_at"] is not None


def test_deleting_and_recreating_an_edge_cannot_dodge_the_gate(
    auth_client: httpx.Client,
):
    """The bypass this gate exists for: a staged raise can be thrown away by
    deleting the edge, so re-adding it public has to hit the same door."""
    _, a, b = _exposing_pair(auth_client)
    t = _type(auth_client)
    edge = _edge(auth_client, a, b, t)
    assert _raise(auth_client, edge).json()["visibility"] == "public"
    _arm_visibility_safety(auth_client)

    assert auth_client.delete(f"/v1/member-relationships/{edge}").status_code == 204

    denied = _create(auth_client, a, b, t, visibility="public")
    assert denied.status_code == 400, denied.text
    assert "password" in denied.text.lower(), denied.text
    staged = _create(
        auth_client, a, b, t, visibility="public", password="testpassword123"
    )
    assert staged.status_code == 201, staged.text
    assert staged.json()["visibility"] == "private"
    assert staged.json()["pending_visibility"] == "public"


def test_create_public_edge_is_instant_when_the_system_is_not_safeguarded(
    auth_client: httpx.Client,
):
    # Category on by default; disarm it to test the ungated create path.
    _, a, b = _exposing_pair(auth_client)
    _disarm_visibility_safety(auth_client)

    r = _create(auth_client, a, b, _type(auth_client), visibility="public")
    assert r.status_code == 201, r.text
    assert r.json()["visibility"] == "public"
    assert r.json()["pending_visibility"] is None


def test_create_public_edge_is_instant_without_a_live_grant(
    auth_client: httpx.Client,
):
    a = _member(auth_client, "CreateNoGrantA")
    b = _member(auth_client, "CreateNoGrantB")
    _view(auth_client, members=[a, b], include_relationships=True)
    _arm_visibility_safety(auth_client)

    r = _create(auth_client, a, b, _type(auth_client), visibility="public")
    assert r.status_code == 201, r.text
    assert r.json()["visibility"] == "public"
    assert r.json()["pending_visibility"] is None


def test_create_below_public_is_never_gated(auth_client: httpx.Client):
    """Only `public` is served, so creating at either lower level exposes
    nothing however published the endpoints are."""
    _, a, b = _exposing_pair(auth_client)
    c = _member(auth_client, "CreateThird")
    t = _type(auth_client)
    _arm_visibility_safety(auth_client)

    for other, level in ((b, "private"), (c, "friends")):
        r = _create(auth_client, a, other, t, visibility=level)
        assert r.status_code == 201, r.text
        assert r.json()["visibility"] == level
        assert r.json()["pending_visibility"] is None


def test_create_public_group_edge_is_never_gated(auth_client: httpx.Client):
    """Nothing projects group edges, so a group edge born public is visible to
    nobody and waits for nothing."""
    _exposing_pair(auth_client)
    g1, g2 = _group(auth_client, "CreateGrpOne"), _group(auth_client, "CreateGrpTwo")
    _arm_visibility_safety(auth_client)

    r = auth_client.post(
        "/v1/group-relationships",
        json={
            "source_id": g1,
            "target_id": g2,
            "relationship_type_id": _type(auth_client),
            "visibility": "public",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["visibility"] == "public"
    assert r.json()["pending_visibility"] is None


# ---------------------------------------------------------------------------
# Lowering: always instant, and always cancels a staged raise
# ---------------------------------------------------------------------------


def test_lowering_is_instant_and_ungated(auth_client: httpx.Client):
    """Going dark never waits and never asks for a credential - public down to
    private, and public down to friends (which serves nobody either, since
    every grant that exists today is public-tier)."""
    a = _member(auth_client, "DropA")
    b = _member(auth_client, "DropB")
    c = _member(auth_client, "DropC")
    vid = _view(auth_client, members=[a, b, c], include_relationships=True)
    _publish(auth_client, vid)
    t = _type(auth_client)
    to_private = _edge(auth_client, a, b, t)
    to_friends = _edge(auth_client, a, c, t)
    # Raised before the safeguard is armed, so both start out live-public.
    for edge in (to_private, to_friends):
        assert _raise(auth_client, edge).json()["visibility"] == "public"
    _arm_visibility_safety(auth_client)

    down = auth_client.patch(
        f"/v1/member-relationships/{to_private}", json={"visibility": "private"}
    )
    assert down.status_code == 200, down.text
    assert down.json()["visibility"] == "private"
    assert down.json()["pending_visibility"] is None
    assert down.json()["visibility_activates_at"] is None

    sideways = auth_client.patch(
        f"/v1/member-relationships/{to_friends}", json={"visibility": "friends"}
    )
    assert sideways.status_code == 200, sideways.text
    assert sideways.json()["visibility"] == "friends"
    assert sideways.json()["pending_visibility"] is None


def test_lowering_cancels_a_staged_raise(auth_client: httpx.Client):
    """A staged raise is dropped outright rather than queued behind the
    lowering: the owner changing their mind must not leave a raise armed."""
    edge, _, _, _ = _publishable_edge(auth_client)
    _arm_visibility_safety(auth_client)
    staged = _raise(auth_client, edge, password="testpassword123")
    assert staged.json()["pending_visibility"] == "public"

    cancelled = auth_client.patch(
        f"/v1/member-relationships/{edge}", json={"visibility": "private"}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["visibility"] == "private"
    assert cancelled.json()["pending_visibility"] is None
    assert cancelled.json()["visibility_activates_at"] is None


# ---------------------------------------------------------------------------
# The finalize sweep
# ---------------------------------------------------------------------------


def test_finalize_job_promotes_a_staged_edge_raise(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    edge, _, a, _ = _publishable_edge(auth_client)
    _arm_visibility_safety(auth_client)
    staged = _raise(auth_client, edge, password="testpassword123")
    assert staged.json()["visibility"] == "private"

    _backdate_edge_raise(edge)
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "success"

    promoted = next(
        e
        for e in auth_client.get(f"/v1/members/{a}/relationships").json()
        if e["id"] == edge
    )
    assert promoted["visibility"] == "public"
    assert promoted["pending_visibility"] is None
    assert promoted["visibility_activates_at"] is None


def test_finalize_job_promotes_a_raise_staged_at_create(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    """A raise staged by CREATE is the same staged raise, so the same sweep
    finishes it - the create path is not a second, unswept lifecycle."""
    _, a, b = _exposing_pair(auth_client)
    _arm_visibility_safety(auth_client)
    created = _create(
        auth_client, a, b, _type(auth_client), visibility="public",
        password="testpassword123",
    )
    assert created.status_code == 201, created.text
    edge = created.json()["id"]

    _backdate_edge_raise(edge)
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text

    promoted = next(
        e
        for e in auth_client.get(f"/v1/members/{a}/relationships").json()
        if e["id"] == edge
    )
    assert promoted["visibility"] == "public"
    assert promoted["pending_visibility"] is None
    assert promoted["visibility_activates_at"] is None


# ---------------------------------------------------------------------------
# Group edges: nothing projects them, so nothing gates them
# ---------------------------------------------------------------------------


def test_group_edge_raise_is_never_gated(auth_client: httpx.Client):
    """Same armed system and published view as the deferred member case; a
    group edge still goes public on the spot, with no credential and nothing
    staged, because no surface draws it."""
    m = _member(auth_client, "GroupEdgeMember")
    vid = _view(auth_client, members=[m], include_relationships=True)
    _publish(auth_client, vid)
    g1, g2 = _group(auth_client, "RelGrpOne"), _group(auth_client, "RelGrpTwo")
    t = _type(auth_client)
    created = auth_client.post(
        "/v1/group-relationships",
        json={"source_id": g1, "target_id": g2, "relationship_type_id": t},
    )
    assert created.status_code == 201, created.text
    edge = created.json()["id"]
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(
        f"/v1/group-relationships/{edge}", json={"visibility": "public"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "public"
    # Group edges have no staging columns at all; the read schema reports null.
    assert r.json()["pending_visibility"] is None
    assert r.json()["visibility_activates_at"] is None


# ---------------------------------------------------------------------------
# Reorienting an edge: instant, ungated, and blind to what privacy is doing
# ---------------------------------------------------------------------------


def _directional_type(c: httpx.Client) -> str:
    return _type(
        c, symmetry="directional", forward_label="parent", reverse_label="child"
    )


def _row(c: httpx.Client, node_id: str, edge_id: str) -> dict:
    rows = c.get(f"/v1/members/{node_id}/relationships").json()
    return next(e for e in rows if e["id"] == edge_id)


def test_flip_swaps_the_labels_on_a_directional_edge(auth_client: httpx.Client):
    a, b = _member(auth_client, "FlipA"), _member(auth_client, "FlipB")
    edge = _edge(auth_client, a, b, _directional_type(auth_client))
    assert _row(auth_client, a, edge)["label"] == "parent"

    flipped = auth_client.patch(
        f"/v1/member-relationships/{edge}", json={"flip": True}
    )
    assert flipped.status_code == 200, flipped.text
    assert flipped.json()["source_id"] == b
    assert flipped.json()["target_id"] == a

    # The whole point: both endpoints now read the other label.
    assert _row(auth_client, a, edge)["label"] == "child"
    assert _row(auth_client, a, edge)["direction"] == "incoming"
    assert _row(auth_client, b, edge)["label"] == "parent"


def test_flip_is_refused_on_a_symmetric_edge(auth_client: httpx.Client):
    """A symmetric edge stores its pair in uuid order for stable dedup, so
    there is no direction to reverse - and quietly doing nothing would leave
    the caller believing it had been reversed."""
    a, b = _member(auth_client, "SymA"), _member(auth_client, "SymB")
    edge = _edge(auth_client, a, b, _type(auth_client))
    before = _row(auth_client, a, edge)

    refused = auth_client.patch(
        f"/v1/member-relationships/{edge}", json={"flip": True}
    )
    assert refused.status_code == 400, refused.text
    assert _row(auth_client, a, edge) == before


def test_mutual_is_normalised_the_way_create_normalises_it(
    auth_client: httpx.Client,
):
    """`mutual` only means anything on an `either` type; asking for it on a
    directional one stores false, exactly as create does."""
    a, b = _member(auth_client, "MutA"), _member(auth_client, "MutB")
    c = _member(auth_client, "MutC")
    either = _type(
        auth_client,
        symmetry="either",
        forward_label="protector",
        reverse_label="protectee",
    )
    either_edge = _edge(auth_client, a, b, either)
    directional_edge = _edge(auth_client, a, c, _directional_type(auth_client))

    on = auth_client.patch(
        f"/v1/member-relationships/{either_edge}", json={"mutual": True}
    )
    assert on.status_code == 200, on.text
    assert on.json()["mutual"] is True
    # Both ends of a mutual either-edge read the forward label, with no
    # direction to speak of.
    assert _row(auth_client, b, either_edge)["label"] == "protector"
    assert _row(auth_client, b, either_edge)["direction"] == "none"

    off = auth_client.patch(
        f"/v1/member-relationships/{either_edge}", json={"mutual": False}
    )
    assert off.json()["mutual"] is False
    assert _row(auth_client, b, either_edge)["label"] == "protectee"

    ignored = auth_client.patch(
        f"/v1/member-relationships/{directional_edge}", json={"mutual": True}
    )
    assert ignored.status_code == 200, ignored.text
    assert ignored.json()["mutual"] is False


def test_reorienting_a_published_edge_is_instant_and_ungated(
    auth_client: httpx.Client,
):
    """The edge is live-public on a published view and the grace window is
    armed - the setup that defers a privacy raise. A flip still lands on the
    spot, with no credential, because it shows the edge to nobody new."""
    _, a, b = _exposing_pair(auth_client)
    edge = _edge(auth_client, a, b, _directional_type(auth_client))
    assert _raise(auth_client, edge).json()["visibility"] == "public"
    _arm_visibility_safety(auth_client)

    flipped = auth_client.patch(
        f"/v1/member-relationships/{edge}", json={"flip": True, "mutual": False}
    )
    assert flipped.status_code == 200, flipped.text
    body = flipped.json()
    assert body["source_id"] == b
    assert body["visibility"] == "public"
    assert body["pending_visibility"] is None
    assert body["visibility_activates_at"] is None


def test_flip_does_not_disturb_a_staged_raise(auth_client: httpx.Client):
    """Reorienting is not a way to cancel a pending publication, or to hurry
    one along."""
    _, a, b = _exposing_pair(auth_client)
    edge = _edge(auth_client, a, b, _directional_type(auth_client))
    _arm_visibility_safety(auth_client)
    staged = _raise(auth_client, edge, password="testpassword123").json()
    assert staged["pending_visibility"] == "public"

    flipped = auth_client.patch(
        f"/v1/member-relationships/{edge}", json={"flip": True}
    )
    assert flipped.status_code == 200, flipped.text
    body = flipped.json()
    assert body["source_id"] == b
    assert body["visibility"] == "private"
    assert body["pending_visibility"] == "public"
    assert body["visibility_activates_at"] == staged["visibility_activates_at"]


def test_group_edges_reorient_the_same_way(auth_client: httpx.Client):
    g1, g2 = _group(auth_client, "FlipGrpOne"), _group(auth_client, "FlipGrpTwo")
    t = _directional_type(auth_client)
    created = auth_client.post(
        "/v1/group-relationships",
        json={"source_id": g1, "target_id": g2, "relationship_type_id": t},
    )
    assert created.status_code == 201, created.text
    edge = created.json()["id"]

    flipped = auth_client.patch(
        f"/v1/group-relationships/{edge}", json={"flip": True}
    )
    assert flipped.status_code == 200, flipped.text
    assert flipped.json()["source_id"] == g2

    symmetric_edge = auth_client.post(
        "/v1/group-relationships",
        json={
            "source_id": g1,
            "target_id": g2,
            "relationship_type_id": _type(auth_client),
        },
    ).json()["id"]
    refused = auth_client.patch(
        f"/v1/group-relationships/{symmetric_edge}", json={"flip": True}
    )
    assert refused.status_code == 400, refused.text


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_cannot_patch_another_systems_edge(auth_client: httpx.Client):
    """Same 404 for "no such edge" and "not yours", on both edge endpoints."""
    other = _second_client()
    try:
        a, b = _member(other, "TheirA"), _member(other, "TheirB")
        t = _type(other)
        their_member_edge = _edge(other, a, b, t)
        g1, g2 = _group(other, "TheirGrpOne"), _group(other, "TheirGrpTwo")
        their_group_edge = other.post(
            "/v1/group-relationships",
            json={"source_id": g1, "target_id": g2, "relationship_type_id": t},
        ).json()["id"]

        assert (
            auth_client.patch(
                f"/v1/member-relationships/{their_member_edge}",
                json={"visibility": "public"},
            ).status_code
            == 404
        )
        assert (
            auth_client.patch(
                f"/v1/group-relationships/{their_group_edge}",
                json={"visibility": "public"},
            ).status_code
            == 404
        )

        # Untouched for its real owner.
        theirs = other.get(f"/v1/members/{a}/relationships").json()
        assert next(e for e in theirs if e["id"] == their_member_edge)[
            "visibility"
        ] == "private"
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


def _public_edge_payload(first: str, second: str) -> dict:
    """A file whose member names dedupe onto members that already exist, and
    which asks for a PUBLIC edge between them - plus a public group edge, which
    has nothing to be demoted from."""
    return {
        "version": "2",
        "system": {"name": "Imported Rel System"},
        "members": [
            {"id": "m1", "name": first, "privacy": "public"},
            {"id": "m2", "name": second, "privacy": "public"},
        ],
        "groups": [
            {"id": "g1", "name": "ImportedGrpOne", "member_ids": []},
            {"id": "g2", "name": "ImportedGrpTwo", "member_ids": []},
        ],
        "relationship_types": [
            {
                "id": "rt1", "name": "ImportedPartner", "symmetry": "symmetric",
                "forward_label": "partner", "reverse_label": None,
            }
        ],
        "member_relationships": [
            {
                "source_id": "m1", "target_id": "m2",
                "relationship_type_id": "rt1", "mutual": False,
                "visibility": "public",
            }
        ],
        "group_relationships": [
            {
                "source_id": "g1", "target_id": "g2",
                "relationship_type_id": "rt1", "mutual": False,
                "visibility": "public",
            }
        ],
        "fronts": [],
        "tags": [],
        "custom_fields": [],
    }


def _imported_levels(c: httpx.Client) -> tuple[str, str]:
    """(member edge visibility, group edge visibility) after an import that
    creates exactly one of each."""
    dump = c.get("/v1/export").json()
    assert len(dump["member_relationships"]) == 1, dump["member_relationships"]
    assert len(dump["group_relationships"]) == 1, dump["group_relationships"]
    return (
        dump["member_relationships"][0]["visibility"],
        dump["group_relationships"][0]["visibility"],
    )


def test_import_demotes_a_public_edge_between_exposed_members(
    auth_client: httpx.Client,
):
    """Restoring a backup must not publish a relationship. The owner-side
    raise has step-up and a grace window in front of it; an import has neither,
    so the level is dropped to private and the user is told."""
    a = _member(auth_client, "ImpExposedA")
    b = _member(auth_client, "ImpExposedB")
    vid = _view(auth_client, members=[a, b], include_relationships=True)
    _publish(auth_client, vid)

    job = _import(auth_client, _public_edge_payload("ImpExposedA", "ImpExposedB"))
    assert "were marked public in the file" in _messages(job), job["events"]

    member_level, group_level = _imported_levels(auth_client)
    assert member_level == "private"
    # Nothing projects group edges, so there was never anything to demote.
    assert group_level == "public"


def test_import_keeps_a_public_edge_when_the_view_excludes_relationships(
    auth_client: httpx.Client,
):
    """The important half: the guard must not flatten every import. With the
    flag off nothing would have been drawn, so the file's level stands."""
    a = _member(auth_client, "ImpNoFlagA")
    b = _member(auth_client, "ImpNoFlagB")
    vid = _view(auth_client, members=[a, b], include_relationships=False)
    _publish(auth_client, vid)

    job = _import(auth_client, _public_edge_payload("ImpNoFlagA", "ImpNoFlagB"))
    assert "were marked public in the file" not in _messages(job), job["events"]
    assert _imported_levels(auth_client)[0] == "public"


def test_import_keeps_a_public_edge_without_a_live_grant(auth_client: httpx.Client):
    """A curated but unpublished view points at nobody, so an imported public
    edge is not published either."""
    a = _member(auth_client, "ImpNoGrantA")
    b = _member(auth_client, "ImpNoGrantB")
    _view(auth_client, members=[a, b], include_relationships=True)

    job = _import(auth_client, _public_edge_payload("ImpNoGrantA", "ImpNoGrantB"))
    assert "were marked public in the file" not in _messages(job), job["events"]
    assert _imported_levels(auth_client)[0] == "public"


def test_import_keeps_a_public_edge_when_an_endpoint_is_not_public(
    auth_client: httpx.Client,
):
    """The member ceiling stops the edge being drawn whatever it says, so
    there is no exposure for the importer to head off."""
    a = _member(auth_client, "ImpCeilingA")
    b = _member(auth_client, "ImpCeilingB", privacy="private")
    vid = _view(auth_client, members=[a, b], include_relationships=True)
    _publish(auth_client, vid)

    job = _import(auth_client, _public_edge_payload("ImpCeilingA", "ImpCeilingB"))
    assert "were marked public in the file" not in _messages(job), job["events"]
    assert _imported_levels(auth_client)[0] == "public"
