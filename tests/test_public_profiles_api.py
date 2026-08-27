"""HTTP-level tests for the anonymous public-profile surface.

The `public_profiles` mark routes these to the run_tests.sh config that runs the
anonymous-surface tests. The `test_disabled_*` cases carry the opposite mark,
`public_profiles_off`, and prove the router 404s wholesale on the config that
runs with PUBLIC_PROFILES_ENABLED=false - they used to be simply unmarked, back
when every other config had the surface off.

The point of this file is the fail-closed contract: the exact key set of each
public payload is pinned, so a model field can never drift into the anonymous
surface unnoticed.
"""

import asyncio
import base64
import io
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8001")

pytestmark = pytest.mark.selfhosted


# ---------------------------------------------------------------------------
# Helpers (owner side: build a system, view, and grant to publish)
# ---------------------------------------------------------------------------


def _register() -> httpx.Client:
    c = httpx.Client(base_url=BASE_URL)
    email = f"pub-{uuid.uuid4().hex[:8]}@sheaf.dev"
    r = c.post("/v1/auth/register", json={"email": email, "password": "testpassword123"})
    assert r.status_code == 201, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


def _member(c: httpx.Client, name: str, **kw) -> str:
    # Default privacy=public so members actually project; the privacy ceiling
    # (a private/friends member never shows on the public tier) has dedicated
    # tests below.
    body = {"name": name, "privacy": "public", **kw}
    r = c.post("/v1/members", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload(c: httpx.Client, purpose: str = "avatar") -> str:
    """Upload a 1x1 PNG and return its storage key."""
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    r = c.post(
        "/v1/files/upload",
        params={"purpose": purpose},
        files={"file": ("a.png", io.BytesIO(png), "image/png")},
    )
    assert r.status_code == 200, r.text
    return r.json()["key"]


def _anon() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL)


def _go_public(c: httpx.Client) -> None:
    """System privacy is the master ceiling over the public surface, so a system
    has to be public before it can publish anything at all."""
    r = c.patch("/v1/systems/me", json={"privacy": "public"})
    assert r.status_code == 200, r.text


def _published_system(
    c: httpx.Client,
    *,
    members: list[str] | None = None,
    include_members: bool = True,
    include_bio: bool = False,
    include_fronting: bool = False,
    fronting_show_count: bool = True,
    include_relationships: bool = False,
    include_groups: bool = False,
    member_permalinks: bool = False,
) -> tuple[str, str]:
    """Create a view with the given members, publish it publicly, and return
    (system_id, view_id)."""
    _go_public(c)
    c.post("/v1/auth/me/attest-adult")
    view = c.post(
        "/v1/share-views",
        json={
            "name": f"V-{uuid.uuid4().hex[:6]}",
            "include_members": include_members,
            "include_bio": include_bio,
            "include_fronting": include_fronting,
            "fronting_show_count": fronting_show_count,
            "include_relationships": include_relationships,
            "include_groups": include_groups,
            "member_permalinks": member_permalinks,
        },
    ).json()["id"]
    for m in members or []:
        r = c.post(f"/v1/share-views/{view}/members", json={"member_id": m})
        assert r.status_code == 200, r.text
    grant = c.post("/v1/share-grants", json={"view_id": view, "subject_type": "public"})
    assert grant.status_code == 201, grant.text
    system_id = c.get("/v1/systems/me").json()["id"]
    return system_id, view


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


def _arm_visibility_stepup(c: httpx.Client, *, grace: int = 0) -> None:
    """Arm a real step-up: a password auth tier on the (already-default-on)
    profile_visibility category, with the grace window at `grace` days. With
    grace 0 an exposing raise re-auths and lands immediately; with grace > 0 it
    also stages behind the window."""
    r = c.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": grace,
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


def _backdate_system_privacy(system_id: str) -> None:
    async def _work(db) -> None:
        from sheaf.models.system import System

        system = await db.get(System, uuid.UUID(system_id))
        assert system is not None and system.privacy_activates_at is not None
        system.privacy_activates_at = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)


def _in_db(work) -> None:
    """Run `work(db)` straight against the test database, then commit.

    Only used to put a pending row's activation time in the past, which the
    API deliberately offers no way to do.
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


def _backdate_view_flags(view_id: str) -> None:
    async def _work(db) -> None:
        from sheaf.models.share import ShareView

        view = await db.get(ShareView, uuid.UUID(view_id))
        assert view is not None and view.flags_activate_at is not None
        view.flags_activate_at = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)


def _backdate_pending_membership(member_id: str) -> None:
    async def _work(db) -> None:
        from sqlalchemy import select

        from sheaf.models.share import ShareItemStatus, ShareViewMember

        result = await db.execute(
            select(ShareViewMember).where(
                ShareViewMember.member_id == uuid.UUID(member_id),
                ShareViewMember.status == ShareItemStatus.PENDING.value,
            )
        )
        rows = list(result.scalars().all())
        assert rows
        for row in rows:
            row.activates_at = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)


def _set_never_shareable(member_id: str) -> None:
    """Set the flag straight in the database, so the member's view rows stay
    put. The API also removes them, which would make a projection test prove
    only "the member left the view"."""

    async def _work(db) -> None:
        from sheaf.models.member import Member

        member = await db.get(Member, uuid.UUID(member_id))
        assert member is not None
        member.never_shareable = True

    _in_db(_work)


def _link_token(c: httpx.Client, view_id: str) -> str:
    _go_public(c)
    r = c.post("/v1/share-grants", json={"view_id": view_id, "subject_type": "link"})
    assert r.status_code == 201, r.text
    return r.json()["token"]


def _rel_type(c: httpx.Client, name: str, **kw) -> str:
    body = {
        "name": name,
        "symmetry": "symmetric",
        "forward_label": "partner",
        **kw,
    }
    r = c.post("/v1/relationship-types", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _edge(
    c: httpx.Client,
    source: str,
    target: str,
    type_id: str,
    visibility: str = "public",
) -> str:
    r = c.post(
        "/v1/member-relationships",
        json={
            "source_id": source,
            "target_id": target,
            "relationship_type_id": type_id,
            "visibility": visibility,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Feature disabled - the public_profiles_off config row
# ---------------------------------------------------------------------------


@pytest.mark.public_profiles_off
def test_disabled_public_system_404s(client: httpx.Client):
    r = client.get(f"/v1/public/systems/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.public_profiles_off
def test_disabled_public_shared_404s(client: httpx.Client):
    r = client.get(f"/v1/public/shared/{uuid.uuid4().hex}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Enabled surface
# ---------------------------------------------------------------------------


@pytest.mark.public_profiles
def test_public_system_view_key_set():
    """The public system payload has EXACTLY these keys - the fail-closed
    contract. A new System column must not appear here without a decision."""
    owner = _register()
    m = _member(owner, "Shown")
    system_id, _ = _published_system(owner, members=[m])

    body = _anon().get(f"/v1/public/systems/{system_id}").json()
    assert set(body) == {
        "id", "name", "description", "avatar_url", "color", "tag", "member_count",
        "member_permalinks",
    }
    assert body["member_count"] == 1
    # Presentation configuration the client reads to decide whether a member
    # card links to a page of its own. Off unless the view says otherwise.
    assert body["member_permalinks"] is False
    # Age of a system is deliberately not exposed.
    assert "created_at" not in body
    owner.close()


@pytest.mark.public_profiles
def test_public_member_view_key_set():
    owner = _register()
    m = _member(owner, "Alpha", pronouns="they/them")
    system_id, _ = _published_system(owner, members=[m])

    members = _anon().get(f"/v1/public/systems/{system_id}/members").json()
    assert len(members) == 1
    assert set(members[0]) == {
        "id", "name", "pronouns", "avatar_url", "banner_url",
        "color", "bio", "fields",
    }
    # note / privacy / birthday / created_at etc. must never appear.
    assert "note" not in members[0]
    assert "privacy" not in members[0]
    # One name field, and it is the shown one. `display_name` is gone rather
    # than renamed: carrying both meant a member who set a display name so
    # strangers would not read their own name had it in the payload anyway,
    # one key along, for anything reading the JSON instead of the page.
    assert "display_name" not in members[0]
    owner.close()


@pytest.mark.public_profiles
def test_only_view_members_are_public():
    owner = _register()
    shown = _member(owner, "Shown")
    _member(owner, "NotInView")  # exists but not added to the view
    system_id, _ = _published_system(owner, members=[shown])

    names = {m["name"] for m in _anon().get(f"/v1/public/systems/{system_id}/members").json()}
    assert names == {"Shown"}
    assert "NotInView" not in names
    owner.close()


@pytest.mark.public_profiles
def test_never_shareable_member_never_projected():
    """Even if somehow present in a view, a never-shareable member is filtered
    by the projection query itself."""
    owner = _register()
    m = _member(owner, "Secret")
    system_id, view = _published_system(owner, members=[m])
    # Now mark them never-shareable; it also pulls them from the view.
    owner.patch(f"/v1/members/{m}", json={"never_shareable": True})

    members = _anon().get(f"/v1/public/systems/{system_id}/members").json()
    assert members == []
    # member_count reflects the guard too.
    assert _anon().get(f"/v1/public/systems/{system_id}").json()["member_count"] == 0
    owner.close()


@pytest.mark.public_profiles
def test_private_member_in_view_is_not_projected():
    """member.privacy is an exposure ceiling: a private (default) member added
    to a view never shows on the public tier, and isn't counted."""
    owner = _register()
    shown = _member(owner, "PublicOne", privacy="public")
    hidden = _member(owner, "PrivateOne", privacy="private")
    system_id, view = _published_system(owner, members=[shown])
    # Add the private member to the same view explicitly.
    r = owner.post(f"/v1/share-views/{view}/members", json={"member_id": hidden})
    assert r.status_code == 200, r.text

    names = {m["name"] for m in _anon().get(f"/v1/public/systems/{system_id}/members").json()}
    assert names == {"PublicOne"}
    # member_count reflects only the projectable member.
    assert _anon().get(f"/v1/public/systems/{system_id}").json()["member_count"] == 1
    owner.close()


@pytest.mark.public_profiles
def test_friends_member_not_on_public_tier():
    """A friends-privacy member is off the public tier too (both public and
    link grants are public-tier today)."""
    owner = _register()
    shown = _member(owner, "Pub", privacy="public")
    friend = _member(owner, "FriendOnly", privacy="friends")
    system_id, view = _published_system(owner, members=[shown])
    owner.post(f"/v1/share-views/{view}/members", json={"member_id": friend})

    names = {m["name"] for m in _anon().get(f"/v1/public/systems/{system_id}/members").json()}
    assert names == {"Pub"}
    owner.close()


@pytest.mark.public_profiles
def test_bio_only_shown_when_view_includes_it():
    owner = _register()
    m = _member(owner, "Biod", description="my bio text")

    # Without include_bio: bio is null.
    sys_no_bio, _ = _published_system(owner, members=[m], include_bio=False)
    mem = _anon().get(f"/v1/public/systems/{sys_no_bio}/members").json()[0]
    assert mem["bio"] is None

    # A second system/owner with include_bio on: bio shows.
    owner2 = _register()
    m2 = _member(owner2, "Biod2", description="second bio")
    sys_bio, _ = _published_system(owner2, members=[m2], include_bio=True)
    mem2 = _anon().get(f"/v1/public/systems/{sys_bio}/members").json()[0]
    assert mem2["bio"] == "second bio"
    owner.close()
    owner2.close()


@pytest.mark.public_profiles
def test_only_exposed_custom_fields_appear():
    owner = _register()
    m = _member(owner, "Fielded")
    shown_field = owner.post("/v1/fields", json={"name": "Role", "field_type": "text"}).json()
    secret_field = owner.post("/v1/fields", json={"name": "Secret", "field_type": "text"}).json()
    # Give the member a value for both (the endpoint takes a list).
    r = owner.put(
        f"/v1/members/{m}/fields",
        json=[
            {"field_id": shown_field["id"], "value": "Protector"},
            {"field_id": secret_field["id"], "value": "hush"},
        ],
    )
    assert r.status_code == 200, r.text

    _, view = _published_system(owner, members=[m])
    # Expose only the Role field: selected into the view AND raised to public.
    # Both gates are needed since the definition ceiling became enforced; the
    # raise is instant here because the profile-visibility category, though on
    # by default, has no grace window and the `none` auth tier makes its step-up
    # a no-op, so nothing stages and nothing is demanded.
    owner.post(f"/v1/share-views/{view}/fields", json={"field_id": shown_field["id"]})
    r = owner.patch(f"/v1/fields/{shown_field['id']}", json={"privacy": "public"})
    assert r.status_code == 200, r.text
    system_id = owner.get("/v1/systems/me").json()["id"]

    mem = _anon().get(f"/v1/public/systems/{system_id}/members").json()[0]
    assert mem["fields"] == {"Role": "Protector"}
    # Secret is doubly held: never selected, and still at the private default.
    assert "Secret" not in mem["fields"]
    owner.close()


# ---------------------------------------------------------------------------
# External images: a visitor's browser is never sent to a host the owner chose
# ---------------------------------------------------------------------------

_TRACKER = "https://tracker.example/pixel.png"
_HIDDEN = "#external-image-hidden"


@pytest.mark.public_profiles
def test_external_avatar_and_banner_are_withheld():
    owner = _register()
    key = _upload(owner)
    hosted = _member(owner, "Hosted", avatar_url=key)
    linked = _member(owner, "Linked", avatar_url=_TRACKER, banner_url=_TRACKER)
    system_id, _ = _published_system(owner, members=[hosted, linked])

    by_name = {
        m["name"]: m
        for m in _anon().get(f"/v1/public/systems/{system_id}/members").json()
    }
    assert by_name["Linked"]["avatar_url"] is None
    assert by_name["Linked"]["banner_url"] is None
    # An upload still resolves to a serve URL (signed only when the config
    # says so - the test stack runs image_serving=unsigned).
    assert by_name["Hosted"]["avatar_url"].startswith(f"/v1/public/files/{key}")

    # Scope guard: the owner's own read is unchanged. Nothing was scrubbed from
    # the row, it is only withheld from the anonymous surface.
    own = owner.get(f"/v1/members/{linked}").json()
    assert own["avatar_url"] == _TRACKER
    assert own["banner_url"] == _TRACKER
    owner.close()


@pytest.mark.public_profiles
def test_external_bio_image_is_replaced_by_the_sentinel():
    owner = _register()
    key = _upload(owner, purpose="bio")
    desc = f"Before ![mine](/v1/files/{key}) middle ![theirs]({_TRACKER}) after"
    m = _member(owner, "Bioed", description=desc)
    system_id, _ = _published_system(owner, members=[m], include_bio=True)

    bio = _anon().get(f"/v1/public/systems/{system_id}/members").json()[0]["bio"]
    assert "tracker.example" not in bio
    assert _HIDDEN in bio
    assert f"/v1/public/files/{key}" in bio
    assert bio.startswith("Before ") and bio.endswith(" after")

    # Same scope guard, on the authenticated read.
    assert _TRACKER in owner.get(f"/v1/members/{m}").json()["description"]
    owner.close()


@pytest.mark.public_profiles
def test_system_description_hides_external_images_too():
    """The system description is rendered as markdown on the public page, so it
    gets the same treatment as a bio."""
    owner = _register()
    key = _upload(owner, purpose="bio")
    r = owner.patch(
        "/v1/systems/me",
        json={"description": f"![logo](/v1/files/{key}) ![pixel]({_TRACKER})"},
    )
    assert r.status_code == 200, r.text
    system_id, _ = _published_system(owner)

    body = _anon().get(f"/v1/public/systems/{system_id}").json()
    assert "tracker.example" not in body["description"]
    assert _HIDDEN in body["description"]
    assert f"/v1/public/files/{key}" in body["description"]

    assert _TRACKER in owner.get("/v1/systems/me").json()["description"]
    owner.close()


@pytest.mark.public_profiles
def test_fronting_member_external_avatar_is_withheld():
    owner = _register()
    m = _member(owner, "Fronter", avatar_url=_TRACKER)
    system_id, _ = _published_system(owner, members=[m], include_fronting=True)
    assert owner.post("/v1/fronts", json={"member_ids": [m]}).status_code == 201

    fronting = _anon().get(f"/v1/public/systems/{system_id}/fronting").json()
    assert [pm["name"] for pm in fronting["members"]] == ["Fronter"]
    assert fronting["members"][0]["avatar_url"] is None
    owner.close()


# ---------------------------------------------------------------------------
# Deferred exposure: the surface only moves once the sweep says so
# ---------------------------------------------------------------------------


@pytest.mark.public_profiles
def test_raising_a_member_to_public_waits_for_the_sweep(admin_client: httpx.Client):
    """Flipping a member in a published view to public does not publish them
    on the spot; their membership row is demoted until the window elapses."""
    owner = _register()
    m = _member(owner, "Riser", privacy="private")
    system_id, _ = _published_system(owner, members=[m])
    _arm_visibility_safety(owner)

    r = owner.patch(
        f"/v1/members/{m}", json={"privacy": "public", "password": "testpassword123"}
    )
    assert r.status_code == 200, r.text
    assert _anon().get(f"/v1/public/systems/{system_id}/members").json() == []

    _backdate_pending_membership(m)
    assert (
        admin_client.post(
            "/v1/admin/jobs/finalize_share_activations/run"
        ).status_code
        == 200
    )

    names = {
        mem["name"]
        for mem in _anon().get(f"/v1/public/systems/{system_id}/members").json()
    }
    assert names == {"Riser"}
    owner.close()


@pytest.mark.public_profiles
def test_lowering_a_member_from_public_hides_immediately():
    """Un-exposing never waits, even with the grace window armed."""
    owner = _register()
    m = _member(owner, "Fading")
    system_id, _ = _published_system(owner, members=[m])
    assert len(_anon().get(f"/v1/public/systems/{system_id}/members").json()) == 1
    _arm_visibility_safety(owner)

    # No credential needed, and no window served.
    r = owner.patch(f"/v1/members/{m}", json={"privacy": "private"})
    assert r.status_code == 200, r.text
    assert _anon().get(f"/v1/public/systems/{system_id}/members").json() == []
    owner.close()


@pytest.mark.public_profiles
def test_view_flag_loosening_reaches_the_surface_only_after_the_sweep(
    admin_client: httpx.Client,
):
    owner = _register()
    m = _member(owner, "Biod", description="my bio text")
    system_id, view = _published_system(owner, members=[m], include_bio=False)
    _arm_visibility_safety(owner)

    r = owner.patch(
        f"/v1/share-views/{view}",
        json={"include_bio": True, "password": "testpassword123"},
    )
    assert r.status_code == 200, r.text
    assert _anon().get(f"/v1/public/systems/{system_id}/members").json()[0][
        "bio"
    ] is None

    _backdate_view_flags(view)
    assert (
        admin_client.post(
            "/v1/admin/jobs/finalize_share_activations/run"
        ).status_code
        == 200
    )

    mem = _anon().get(f"/v1/public/systems/{system_id}/members").json()[0]
    assert mem["bio"] == "my bio text"
    owner.close()


# ---------------------------------------------------------------------------
# 404 matrix (no existence oracle)
# ---------------------------------------------------------------------------


@pytest.mark.public_profiles
def test_unknown_system_404s():
    assert _anon().get(f"/v1/public/systems/{uuid.uuid4()}").status_code == 404


@pytest.mark.public_profiles
def test_private_system_404s():
    """A system with no public grant looks identical to one that never existed."""
    owner = _register()
    _member(owner, "X")
    system_id = owner.get("/v1/systems/me").json()["id"]
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 404
    owner.close()


@pytest.mark.public_profiles
def test_revoked_grant_404s():
    owner = _register()
    m = _member(owner, "Gone")
    system_id, view = _published_system(owner, members=[m])
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 200

    gid = next(
        g["id"] for g in owner.get("/v1/share-grants").json()
        if g["subject_type"] == "public"
    )
    owner.delete(f"/v1/share-grants/{gid}")
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 404
    owner.close()


@pytest.mark.public_profiles
def test_bad_link_token_404s():
    assert _anon().get(f"/v1/public/shared/{uuid.uuid4().hex}").status_code == 404


@pytest.mark.public_profiles
def test_rotated_link_token_dies():
    owner = _register()
    m = _member(owner, "Linked")
    owner.post("/v1/auth/me/attest-adult")
    view = owner.post("/v1/share-views", json={"name": "L"}).json()["id"]
    owner.post(f"/v1/share-views/{view}/members", json={"member_id": m})
    token = _link_token(owner, view)
    assert _anon().get(f"/v1/public/shared/{token}").status_code == 200

    gid = next(g["id"] for g in owner.get("/v1/share-grants").json() if g["subject_type"] == "link")
    owner.post(f"/v1/share-grants/{gid}/rotate")
    # Old token no longer resolves; identical 404.
    assert _anon().get(f"/v1/public/shared/{token}").status_code == 404
    owner.close()


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


@pytest.mark.public_profiles
def test_noindex_header_present():
    owner = _register()
    m = _member(owner, "Indexed")
    system_id, _ = _published_system(owner, members=[m])
    resp = _anon().get(f"/v1/public/systems/{system_id}")
    assert "noindex" in resp.headers.get("x-robots-tag", "").lower()
    owner.close()


@pytest.mark.public_profiles
def test_token_keyed_responses_are_not_shared_cacheable():
    """The token is in the URL, so only the requesting client may store the
    response; a shared cache would serve it to whoever asked next."""
    owner = _register()
    m = _member(owner, "Linked")
    system_id, view = _published_system(owner, members=[m])
    token = _link_token(owner, view)

    link_resp = _anon().get(f"/v1/public/shared/{token}")
    assert link_resp.status_code == 200, link_resp.text
    assert link_resp.headers["cache-control"] == "private, max-age=60"

    # The public-profile URL carries no secret and stays shared-cacheable.
    public_resp = _anon().get(f"/v1/public/systems/{system_id}")
    assert public_resp.headers["cache-control"] == "public, max-age=60"
    owner.close()


@pytest.mark.public_profiles
def test_failed_link_response_keeps_its_headers():
    """A 404 is built by the exception handler, which never sees the route's
    Response - and 404 is a status a shared cache may store on its own. The
    middleware is what keeps a dead-link answer off intermediaries."""
    resp = _anon().get(f"/v1/public/shared/{uuid.uuid4().hex}")
    assert resp.status_code == 404
    cache = resp.headers["cache-control"]
    assert "private" in cache and "no-store" in cache
    assert "noindex" in resp.headers["x-robots-tag"].lower()


@pytest.mark.public_profiles
def test_expired_link_grant_is_refused_anonymously():
    owner = _register()
    m = _member(owner, "Lapsed")
    _, view = _published_system(owner, members=[m])
    r = owner.post(
        "/v1/share-grants",
        json={
            "view_id": view,
            "subject_type": "link",
            "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
    )
    assert r.status_code == 201, r.text
    token = r.json()["token"]

    # Same 404 as a revoked or never-existent token: expiry is not an oracle.
    assert _anon().get(f"/v1/public/shared/{token}").status_code == 404
    assert _anon().get(f"/v1/public/shared/{token}/members").status_code == 404
    owner.close()


# ---------------------------------------------------------------------------
# Fronting
# ---------------------------------------------------------------------------


@pytest.mark.public_profiles
def test_fronting_404s_when_view_excludes_it():
    owner = _register()
    m = _member(owner, "F")
    system_id, _ = _published_system(owner, members=[m], include_fronting=False)
    assert _anon().get(f"/v1/public/systems/{system_id}/fronting").status_code == 404
    owner.close()


@pytest.mark.public_profiles
def test_fronting_names_public_member_and_counts_others():
    owner = _register()
    shown = _member(owner, "Fronter")
    other = _member(owner, "OtherFronter")
    system_id, _ = _published_system(
        owner, members=[shown], include_fronting=True, fronting_show_count=True
    )
    # Both are fronting; only `shown` is in the view.
    owner.post("/v1/fronts", json={"member_ids": [shown, other]})

    body = _anon().get(f"/v1/public/systems/{system_id}/fronting").json()
    assert set(body) == {"members", "hidden_count"}
    pm = body["members"][0]
    # Lite card: identity + since only. No bio/fields on the fronting surface,
    # even when the view exposes them elsewhere.
    assert set(pm) == {
        "id", "name", "pronouns", "avatar_url", "color", "since",
    }
    # Same single-name rule as the member card, and it matters most here: this
    # is the surface a watcher polls every minute.
    assert "display_name" not in pm
    assert pm["name"] == "Fronter"
    assert pm["since"] is not None
    # `other` is public-but-not-in-view -> counted, not named.
    assert body["hidden_count"] == 1
    owner.close()


@pytest.mark.public_profiles
def test_fronting_private_member_not_even_counted():
    owner = _register()
    shown = _member(owner, "Visible")
    private = _member(owner, "PrivateFronter")
    owner.patch(f"/v1/members/{private}", json={"fronting_private": True})
    system_id, _ = _published_system(
        owner, members=[shown], include_fronting=True, fronting_show_count=True
    )
    owner.post("/v1/fronts", json={"member_ids": [shown, private]})

    body = _anon().get(f"/v1/public/systems/{system_id}/fronting").json()
    assert {pm["name"] for pm in body["members"]} == {"Visible"}
    # fronting_private member's front state does not propagate, not even as a count.
    assert body["hidden_count"] == 0
    owner.close()


@pytest.mark.public_profiles
def test_fronting_show_count_off_hides_the_number():
    owner = _register()
    shown = _member(owner, "OnlyShown")
    other = _member(owner, "Uncounted")
    system_id, _ = _published_system(
        owner, members=[shown], include_fronting=True, fronting_show_count=False
    )
    owner.post("/v1/fronts", json={"member_ids": [shown, other]})

    body = _anon().get(f"/v1/public/systems/{system_id}/fronting").json()
    assert {pm["name"] for pm in body["members"]} == {"OnlyShown"}
    assert body["hidden_count"] == 0
    owner.close()


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


@pytest.mark.public_profiles
def test_relationships_404_when_view_excludes_them():
    """Same no-oracle rule as fronting, on both grant shapes: an empty list
    would answer "does this profile share relationships?" on its own."""
    owner = _register()
    a, b = _member(owner, "RelA"), _member(owner, "RelB")
    system_id, view = _published_system(
        owner, members=[a, b], include_relationships=False
    )
    _edge(owner, a, b, _rel_type(owner, "Partner"))
    token = _link_token(owner, view)

    assert _anon().get(f"/v1/public/systems/{system_id}/relationships").status_code == 404
    assert _anon().get(f"/v1/public/shared/{token}/relationships").status_code == 404
    # The rest of the profile still resolves, so the 404 really is the flag.
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 200
    owner.close()


@pytest.mark.public_profiles
def test_public_relationship_key_set():
    """The exact key set of the edge payload and of each endpoint - the same
    fail-closed contract the member and system payloads carry."""
    owner = _register()
    a, b = _member(owner, "KeyA"), _member(owner, "KeyB")
    system_id, _ = _published_system(
        owner, members=[a, b], include_relationships=True
    )
    _edge(owner, a, b, _rel_type(owner, "Partner"))

    body = _anon().get(f"/v1/public/systems/{system_id}/relationships").json()
    assert set(body) == {"relationships"}
    edge = body["relationships"][0]
    assert set(edge) == {
        "id", "type_name", "type_color", "source", "target",
        "source_label", "target_label", "mutual",
    }
    # An endpoint is an id and a name only; the client joins on id for the rest.
    assert set(edge["source"]) == {"id", "name"}
    assert set(edge["target"]) == {"id", "name"}
    assert {edge["source"]["name"], edge["target"]["name"]} == {"KeyA", "KeyB"}
    owner.close()


@pytest.mark.public_profiles
def test_only_public_edges_between_projected_members_appear():
    """An edge is the one payload that names two people at once, so marking it
    public is a request, not an override: every gate the members themselves
    pass through applies to both ends first."""
    owner = _register()
    shown_a, shown_b = _member(owner, "ShownA"), _member(owner, "ShownB")
    private = _member(owner, "PrivateEnd", privacy="private")
    outside = _member(owner, "OutsideEnd")
    secret = _member(owner, "SecretEnd")
    system_id, view = _published_system(
        owner,
        members=[shown_a, shown_b, private, secret],
        include_relationships=True,
    )
    # `outside` is deliberately left out of the view.
    published = _rel_type(owner, "Partner")
    _edge(owner, shown_a, shown_b, published)
    # Each of these is marked public and still must not be drawn.
    _edge(owner, shown_a, private, _rel_type(owner, "WithPrivate"))
    _edge(owner, shown_a, outside, _rel_type(owner, "WithOutside"))
    _edge(owner, shown_a, secret, _rel_type(owner, "WithSecret"))
    # And a friends-level edge between two fully projected members: the edge's
    # own level is a gate too, and every grant today is public-tier.
    _edge(owner, shown_b, shown_a, _rel_type(owner, "Friendsy"), visibility="friends")
    _set_never_shareable(secret)

    body = _anon().get(f"/v1/public/systems/{system_id}/relationships").json()
    assert [e["type_name"] for e in body["relationships"]] == ["Partner"]
    names = {e["source"]["name"] for e in body["relationships"]} | {
        e["target"]["name"] for e in body["relationships"]
    }
    assert names == {"ShownA", "ShownB"}

    # Identical answer through a link grant on the same view.
    token = _link_token(owner, view)
    linked = _anon().get(f"/v1/public/shared/{token}/relationships").json()
    assert [e["type_name"] for e in linked["relationships"]] == ["Partner"]
    owner.close()


@pytest.mark.public_profiles
def test_relationship_labels_read_from_each_end_of_a_directional_type():
    """source_label is the forward label, target_label the reverse one - the
    stored row is canonical and the payload says how it reads from each end."""
    owner = _register()
    parent, child = _member(owner, "TheParent"), _member(owner, "TheChild")
    system_id, _ = _published_system(
        owner, members=[parent, child], include_relationships=True
    )
    rtype = _rel_type(
        owner,
        "ParentChild",
        symmetry="directional",
        forward_label="parent",
        reverse_label="child",
    )
    _edge(owner, parent, child, rtype)

    edge = _anon().get(
        f"/v1/public/systems/{system_id}/relationships"
    ).json()["relationships"][0]
    assert edge["source"]["name"] == "TheParent"
    assert edge["target"]["name"] == "TheChild"
    assert edge["source_label"] == "parent"
    assert edge["target_label"] == "child"
    assert edge["mutual"] is False
    owner.close()


@pytest.mark.public_profiles
def test_relationship_type_colour_rides_along():
    """The type's colour says nothing about anyone, so it is published with the
    edge and must survive the projection unchanged."""
    owner = _register()
    a, b = _member(owner, "ColourA"), _member(owner, "ColourB")
    system_id, _ = _published_system(
        owner, members=[a, b], include_relationships=True
    )
    _edge(owner, a, b, _rel_type(owner, "Tinted", color="#ff8800"))

    body = _anon().get(f"/v1/public/systems/{system_id}/relationships").json()
    assert body["relationships"][0]["type_color"] == "#ff8800"
    owner.close()


# ---------------------------------------------------------------------------
# The member roster as a switch (include_members)
# ---------------------------------------------------------------------------


@pytest.mark.public_profiles
def test_members_404_when_the_roster_is_off():
    """Off makes the roster UNADDRESSABLE, not empty.

    An empty list would answer "does this profile have members?" for anyone who
    asked, which is a fact about the system and not one the owner published.
    The rest of the profile still resolves, so the 404 really is the flag.
    """
    owner = _register()
    m = _member(owner, "Curated")
    system_id, view = _published_system(
        owner, members=[m], include_members=False
    )
    token = _link_token(owner, view)

    assert _anon().get(f"/v1/public/systems/{system_id}/members").status_code == 404
    assert _anon().get(f"/v1/public/shared/{token}/members").status_code == 404
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 200
    owner.close()


@pytest.mark.public_profiles
def test_member_count_is_null_when_the_roster_is_off():
    """A roster the view refuses to serve must not be countable either: "23
    members you cannot see" is exactly the fact being withheld. Null, not zero
    - zero would be a claim, and a false one."""
    owner = _register()
    a, b = _member(owner, "CountA"), _member(owner, "CountB")
    system_id, _ = _published_system(
        owner, members=[a, b], include_members=False
    )

    body = _anon().get(f"/v1/public/systems/{system_id}").json()
    assert body["member_count"] is None
    owner.close()


@pytest.mark.public_profiles
def test_relationships_need_the_roster_too():
    """An edge may only name endpoints the view publishes IN FULL. With the
    roster off it publishes none of them, and an edge endpoint is deliberately
    just an id and a name meant to be joined against /members - so the name in
    the edge would be all a visitor got, which is the exact leak the edge gates
    exist to prevent.

    404, not an empty list, and on both grant types: an empty body answers "is
    the roster off?" for anyone who asks, which is the same oracle the members,
    groups and fronting routes refuse.
    """
    owner = _register()
    a, b = _member(owner, "EdgeA"), _member(owner, "EdgeB")
    system_id, view_id = _published_system(
        owner,
        members=[a, b],
        include_members=False,
        include_relationships=True,
    )
    _edge(owner, a, b, _rel_type(owner, "Partner"))

    r = _anon().get(f"/v1/public/systems/{system_id}/relationships")
    assert r.status_code == 404, r.text

    token = _link_token(owner, view_id)
    r = _anon().get(f"/v1/public/shared/{token}/relationships")
    assert r.status_code == 404, r.text
    owner.close()


@pytest.mark.public_profiles
def test_fronting_is_deliberately_independent_of_the_roster():
    """Turning the roster off is not a member-anonymity switch.

    Fronting is its own surface with its own flag, its own reduced card and its
    own reason to be on - "who is around right now" is published by people who
    want no directory beside it. An owner who wants nobody named anywhere turns
    this off as well, and the UI says so.
    """
    owner = _register()
    m = _member(owner, "Fronter")
    system_id, _ = _published_system(
        owner,
        members=[m],
        include_members=False,
        include_fronting=True,
    )
    r = owner.post("/v1/fronts", json={"member_ids": [m]})
    assert r.status_code == 201, r.text

    body = _anon().get(f"/v1/public/systems/{system_id}/fronting").json()
    assert [pm["name"] for pm in body["members"]] == ["Fronter"]
    owner.close()


# ---------------------------------------------------------------------------
# Groups on a profile
# ---------------------------------------------------------------------------


def _group(c: httpx.Client, name: str, members: list[str] | None = None) -> str:
    r = c.post("/v1/groups", json={"name": name})
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    if members:
        assert (
            c.put(f"/v1/groups/{gid}/members", json={"member_ids": members}).status_code
            == 200
        )
    return gid


def _publish_group(c: httpx.Client, group_id: str) -> None:
    r = c.patch(f"/v1/groups/{group_id}", json={"privacy": "public"})
    assert r.status_code == 200, r.text
    assert r.json()["privacy"] == "public"


@pytest.mark.public_profiles
def test_groups_404_when_the_flag_is_off():
    """Same no-oracle rule as fronting and relationships: an empty list would
    answer "does this profile show groups?" separately from "is this profile
    public?"."""
    owner = _register()
    m = _member(owner, "GroupMember")
    system_id, view = _published_system(owner, members=[m])
    _publish_group(owner, _group(owner, "Published", members=[m]))
    token = _link_token(owner, view)

    assert _anon().get(f"/v1/public/systems/{system_id}/groups").status_code == 404
    assert _anon().get(f"/v1/public/shared/{token}/groups").status_code == 404
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 200
    owner.close()


@pytest.mark.public_profiles
def test_public_group_key_set():
    owner = _register()
    m = _member(owner, "InGroup")
    system_id, _ = _published_system(
        owner, members=[m], include_groups=True
    )
    gid = _group(owner, "Littles", members=[m])
    assert (
        owner.patch(
            f"/v1/groups/{gid}", json={"description": "desc", "color": "#123456"}
        ).status_code
        == 200
    )
    _publish_group(owner, gid)

    body = _anon().get(f"/v1/public/systems/{system_id}/groups").json()
    assert set(body) == {"groups"}
    group = body["groups"][0]
    assert set(group) == {"id", "name", "description", "color", "members"}
    assert group["name"] == "Littles"
    assert group["description"] == "desc"
    assert group["color"] == "#123456"
    # A group member is an id and a name only, like a relationship endpoint.
    assert set(group["members"][0]) == {"id", "name"}
    # parent_id and the staging columns must never reach the public payload.
    assert "parent_id" not in group
    assert "privacy" not in group
    owner.close()


@pytest.mark.public_profiles
def test_only_public_groups_are_projected():
    owner = _register()
    m = _member(owner, "Shared")
    system_id, _ = _published_system(owner, members=[m], include_groups=True)
    _publish_group(owner, _group(owner, "Open", members=[m]))
    _group(owner, "Closed", members=[m])  # left at the default `private`
    friends = _group(owner, "Friendly", members=[m])
    assert (
        owner.patch(f"/v1/groups/{friends}", json={"privacy": "friends"}).status_code
        == 200
    )

    names = {
        g["name"]
        for g in _anon().get(f"/v1/public/systems/{system_id}/groups").json()["groups"]
    }
    assert names == {"Open"}
    owner.close()


@pytest.mark.public_profiles
def test_group_roster_is_an_intersection_not_a_second_allowlist():
    """A published group can never name somebody the view was not already
    naming: its roster is intersected with the members this view projects, so
    every guard the member list obeys applies here without being restated."""
    owner = _register()
    shown = _member(owner, "GroupShown")
    outside = _member(owner, "GroupOutside")
    private = _member(owner, "GroupPrivate", privacy="private")
    secret = _member(owner, "GroupSecret")
    system_id, _ = _published_system(
        owner, members=[shown, private, secret], include_groups=True
    )
    _set_never_shareable(secret)
    _publish_group(
        owner,
        _group(owner, "Everyone", members=[shown, outside, private, secret]),
    )

    group = _anon().get(f"/v1/public/systems/{system_id}/groups").json()["groups"][0]
    assert [gm["name"] for gm in group["members"]] == ["GroupShown"]
    owner.close()


@pytest.mark.public_profiles
def test_public_group_with_an_empty_roster_still_shows():
    """Name, description and colour are what the owner chose to publish about
    the group. An empty roster discloses nothing about who is in it, so hiding
    the group would be withholding something the owner did publish."""
    owner = _register()
    shown = _member(owner, "Elsewhere")
    outside = _member(owner, "NotInView")
    system_id, _ = _published_system(
        owner, members=[shown], include_groups=True
    )
    _publish_group(owner, _group(owner, "Offstage", members=[outside]))

    groups = _anon().get(f"/v1/public/systems/{system_id}/groups").json()["groups"]
    assert [g["name"] for g in groups] == ["Offstage"]
    assert groups[0]["members"] == []
    owner.close()


@pytest.mark.public_profiles
def test_group_roster_is_empty_when_the_member_list_is_off():
    """The roster comes from the same rows /members serves, so with the roster
    off there is nobody to intersect with and the group stands on its own."""
    owner = _register()
    m = _member(owner, "Hidden")
    system_id, _ = _published_system(
        owner, members=[m], include_members=False, include_groups=True
    )
    _publish_group(owner, _group(owner, "Standalone", members=[m]))

    groups = _anon().get(f"/v1/public/systems/{system_id}/groups").json()["groups"]
    assert [g["name"] for g in groups] == ["Standalone"]
    assert groups[0]["members"] == []
    owner.close()


@pytest.mark.public_profiles
def test_group_description_hides_external_images():
    """Group descriptions take the same public resolve pass as a system
    description or a member bio: an external image would make an anonymous
    visitor's browser announce itself to an owner-chosen host."""
    owner = _register()
    m = _member(owner, "GroupImg")
    system_id, _ = _published_system(
        owner, members=[m], include_groups=True
    )
    key = _upload(owner, purpose="bio")
    gid = _group(owner, "Illustrated", members=[m])
    assert (
        owner.patch(
            f"/v1/groups/{gid}",
            json={
                "description": f"![mine](/v1/files/{key}) ![theirs]({_TRACKER})"
            },
        ).status_code
        == 200
    )
    _publish_group(owner, gid)

    desc = _anon().get(
        f"/v1/public/systems/{system_id}/groups"
    ).json()["groups"][0]["description"]
    assert f"/v1/public/files/{key}" in desc
    assert _TRACKER not in desc
    owner.close()


# ---------------------------------------------------------------------------
# Member permalinks
# ---------------------------------------------------------------------------


@pytest.mark.public_profiles
def test_member_permalink_serves_the_same_card_as_the_list():
    owner = _register()
    m = _member(owner, "Linkable", pronouns="they/them")
    system_id, view = _published_system(
        owner, members=[m], member_permalinks=True
    )
    token = _link_token(owner, view)

    # The system payload advertises the flag, so a client knows to link to the
    # member's own address instead of opening the card in place.
    assert _anon().get(f"/v1/public/systems/{system_id}").json()["member_permalinks"]

    listed = _anon().get(f"/v1/public/systems/{system_id}/members").json()[0]
    single = _anon().get(f"/v1/public/systems/{system_id}/members/{m}")
    assert single.status_code == 200, single.text
    assert single.json() == listed
    # Both grant types address it the same way.
    via_link = _anon().get(f"/v1/public/shared/{token}/members/{m}")
    assert via_link.status_code == 200, via_link.text
    assert via_link.json() == listed
    owner.close()


@pytest.mark.public_profiles
def test_member_permalink_404_matrix():
    """Every reason a permalink does not resolve is the same 404: the flag is
    off, the roster is off, the member is not projected, or the member is not
    public. A visitor trying an id from elsewhere learns nothing."""
    owner = _register()
    shown = _member(owner, "PermaShown")
    outside = _member(owner, "PermaOutside")
    private = _member(owner, "PermaPrivate", privacy="private")
    system_id, view = _published_system(
        owner, members=[shown, private], member_permalinks=True
    )
    anon = _anon()

    # Sanity: the one member who should resolve, does.
    assert anon.get(f"/v1/public/systems/{system_id}/members/{shown}").status_code == 200
    # Not in the view; in the view but below the privacy ceiling; nonexistent.
    for mid in (outside, private, str(uuid.uuid4())):
        assert anon.get(
            f"/v1/public/systems/{system_id}/members/{mid}"
        ).status_code == 404

    # Flag off -> even the projected member is unaddressable.
    assert (
        owner.patch(
            f"/v1/share-views/{view}", json={"member_permalinks": False}
        ).status_code
        == 200
    )
    assert anon.get(f"/v1/public/systems/{system_id}/members/{shown}").status_code == 404

    # Roster off (with permalinks back on) -> unaddressable for the other reason.
    assert (
        owner.patch(
            f"/v1/share-views/{view}",
            json={"member_permalinks": True, "include_members": False},
        ).status_code
        == 200
    )
    assert anon.get(f"/v1/public/systems/{system_id}/members/{shown}").status_code == 404
    owner.close()


@pytest.mark.public_profiles
def test_member_permalink_obeys_the_bio_flag():
    """A permalink is `project_members` filtered to one id, so every per-view
    setting applies without being restated - including the one that decides
    whether a bio is served at all."""
    owner = _register()
    m = _member(owner, "BioLink", description="a bio")
    system_id, view = _published_system(
        owner, members=[m], member_permalinks=True
    )

    body = _anon().get(f"/v1/public/systems/{system_id}/members/{m}").json()
    assert body["bio"] is None

    assert (
        owner.patch(f"/v1/share-views/{view}", json={"include_bio": True}).status_code
        == 200
    )
    body = _anon().get(f"/v1/public/systems/{system_id}/members/{m}").json()
    assert body["bio"] == "a bio"
    owner.close()


# ---------------------------------------------------------------------------
# One name per member, everywhere
# ---------------------------------------------------------------------------


@pytest.mark.public_profiles
def test_shown_name_is_the_display_name_when_there_is_one():
    """The whole point of the single field: a member with a display name is
    published under it, and their own name never leaves the account."""
    owner = _register()
    m = _member(owner, "CanonicalName", display_name="What They Go By")
    system_id, _ = _published_system(owner, members=[m])

    body = _anon().get(f"/v1/public/systems/{system_id}/members").json()
    assert body[0]["name"] == "What They Go By"
    assert "CanonicalName" not in str(body)
    owner.close()


@pytest.mark.public_profiles
def test_shown_name_falls_back_to_the_members_name():
    owner = _register()
    m = _member(owner, "JustAName")
    system_id, _ = _published_system(owner, members=[m])

    body = _anon().get(f"/v1/public/systems/{system_id}/members").json()
    assert body[0]["name"] == "JustAName"
    owner.close()


@pytest.mark.public_profiles
def test_no_public_payload_leaks_a_canonical_name():
    """Every surface that names a member, checked at once: the roster, the
    permalink, the fronting card, both ends of an edge, and a group roster.
    A leak on any one of them is a leak."""
    owner = _register()
    a = _member(owner, "SecretA", display_name="Ay")
    b = _member(owner, "SecretB", display_name="Bee")

    rtype = _rel_type(owner, f"Partner-{uuid.uuid4().hex[:6]}")
    _edge(owner, a, b, rtype)

    group = owner.post(
        "/v1/groups", json={"name": "Both", "privacy": "public"}
    ).json()["id"]
    owner.put(f"/v1/groups/{group}/members", json={"member_ids": [a, b]})

    system_id, _ = _published_system(
        owner,
        members=[a, b],
        include_fronting=True,
        include_relationships=True,
        include_groups=True,
        member_permalinks=True,
    )
    owner.post("/v1/fronts", json={"member_ids": [a]})

    anon = _anon()
    surfaces = [
        anon.get(f"/v1/public/systems/{system_id}/members").text,
        anon.get(f"/v1/public/systems/{system_id}/members/{a}").text,
        anon.get(f"/v1/public/systems/{system_id}/fronting").text,
        anon.get(f"/v1/public/systems/{system_id}/relationships").text,
        anon.get(f"/v1/public/systems/{system_id}/groups").text,
    ]
    for raw in surfaces:
        assert "SecretA" not in raw and "SecretB" not in raw
        assert "display_name" not in raw
    # And the display names really are being served, so the assertion above is
    # not passing on an empty page.
    assert "Ay" in surfaces[0] and "Bee" in surfaces[0]
    owner.close()


# ---------------------------------------------------------------------------
# The signer refuses another account's storage keys
# ---------------------------------------------------------------------------


def _seed_group_description(group_id: str, text: str) -> None:
    """Write a group description straight into the database.

    Deliberately around the API: the write handler now strips a foreign key, so
    the only way to reach the projection-side guard is to put a row in the state
    an old write (or a foreign importer) could have left it in. This is exactly
    the "legacy row" case, and it is why the check exists at the signer at all.
    """

    async def _work(db) -> None:
        from sheaf.models.group import Group

        group = await db.get(Group, uuid.UUID(group_id))
        assert group is not None
        group.description = text

    _in_db(_work)


@pytest.mark.public_profiles
def test_group_description_write_strips_another_accounts_key():
    """The write side. Groups used to be the one description with no ownership
    pass at all, so a key from somebody else's namespace was stored verbatim."""
    victim = _register()
    victim_key = _upload(victim, purpose="bio")

    attacker = _register()
    r = attacker.post(
        "/v1/groups",
        json={
            "name": "Grabby",
            "description": f"before ![x](/v1/files/{victim_key}) after",
        },
    )
    assert r.status_code == 201, r.text
    assert victim_key not in r.json()["description"]
    assert "before" in r.json()["description"]

    # And on update, which is the other half of the same door.
    gid = r.json()["id"]
    patched = attacker.patch(
        f"/v1/groups/{gid}",
        json={"description": f"![x](/v1/files/{victim_key})"},
    )
    assert patched.status_code == 200, patched.text
    assert victim_key not in (patched.json()["description"] or "")
    victim.close()
    attacker.close()


@pytest.mark.public_profiles
def test_projection_hides_a_foreign_key_already_in_the_database():
    """The signer side, which is the one that matters for rows that predate the
    write guard: a stale foreign key renders as hidden and is never signed, so
    there is no data to go and scrub."""
    victim = _register()
    victim_key = _upload(victim, purpose="bio")

    attacker = _register()
    gid = attacker.post(
        "/v1/groups", json={"name": "Stale", "privacy": "public"}
    ).json()["id"]
    system_id, _ = _published_system(attacker, include_groups=True)
    _seed_group_description(gid, f"![x](/v1/files/{victim_key})")

    body = _anon().get(f"/v1/public/systems/{system_id}/groups").json()
    served = next(g for g in body["groups"] if g["id"] == gid)
    assert victim_key not in served["description"]
    assert "token=" not in served["description"]
    assert "#external-image-hidden" in served["description"]
    victim.close()
    attacker.close()


@pytest.mark.public_profiles
def test_projection_still_signs_the_owners_own_key():
    """The guard has to be an ownership check, not a blanket refusal."""
    owner = _register()
    own_key = _upload(owner, purpose="bio")
    gid = owner.post(
        "/v1/groups", json={"name": "Mine", "privacy": "public"}
    ).json()["id"]
    system_id, _ = _published_system(owner, include_groups=True)
    _seed_group_description(gid, f"![x](/v1/files/{own_key})")

    body = _anon().get(f"/v1/public/systems/{system_id}/groups").json()
    served = next(g for g in body["groups"] if g["id"] == gid)
    assert f"/v1/public/files/{own_key}" in served["description"]
    assert "token=" in served["description"]
    owner.close()


# ---------------------------------------------------------------------------
# System privacy and account state suppress the whole surface
# ---------------------------------------------------------------------------


def _set_account_status(email: str, status: str) -> None:
    """Put an account into a moderation state directly.

    There is no self-service route to suspended or banned, and the deletion
    request has its own flow with its own side effects; the point here is the
    resolver's behaviour in each state, not how the state was reached.
    """

    async def _work(db) -> None:
        from sqlalchemy import select

        from sheaf.crypto import blind_index
        from sheaf.models.user import User

        row = await db.execute(
            select(User).where(User.email_hash == blind_index(email))
        )
        user = row.scalar_one()
        user.account_status = status

    _in_db(_work)


def _registered() -> tuple[httpx.Client, str]:
    """_register, but hand back the email so the account state can be set."""
    c = httpx.Client(base_url=BASE_URL)
    email = f"sup-{uuid.uuid4().hex[:8]}@sheaf.dev"
    r = c.post(
        "/v1/auth/register", json={"email": email, "password": "testpassword123"}
    )
    assert r.status_code == 201, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c, email


def _both_grant_urls(owner: httpx.Client) -> tuple[str, str, str]:
    """Publish a public profile AND a share link off one view.

    Both are returned so every suppression case can be asserted against both
    grant kinds: an unlisted link is not a lesser tier that survives a
    suppression, it publishes the same payloads to the same anonymous readers.
    """
    m = _member(owner, "Suppressed")
    system_id, view = _published_system(owner, members=[m])
    return system_id, _link_token(owner, view), view


@pytest.mark.public_profiles
def test_setting_the_system_private_takes_the_whole_surface_down():
    owner = _register()
    system_id, token, _ = _both_grant_urls(owner)

    anon = _anon()
    assert anon.get(f"/v1/public/systems/{system_id}").status_code == 200
    assert anon.get(f"/v1/public/shared/{token}").status_code == 200

    assert owner.patch(
        "/v1/systems/me", json={"privacy": "private"}
    ).status_code == 200

    for path in (f"/v1/public/systems/{system_id}", f"/v1/public/shared/{token}"):
        r = anon.get(path)
        assert r.status_code == 404
        # The uniform 404 - no hint that this is a privacy setting rather than
        # a profile that never existed.
        assert r.json()["detail"] == "Not found"

    # Every sub-route too, not just the entry point.
    for suffix in ("/members", "/fronting", "/relationships", "/groups"):
        assert (
            anon.get(f"/v1/public/systems/{system_id}{suffix}").status_code == 404
        )
    owner.close()


@pytest.mark.public_profiles
def test_friends_privacy_suppresses_exactly_like_private():
    """Every grant that exists today is public-tier, so the parked friends
    level serves nobody either."""
    owner = _register()
    system_id, token, _ = _both_grant_urls(owner)
    owner.patch("/v1/systems/me", json={"privacy": "friends"})

    anon = _anon()
    assert anon.get(f"/v1/public/systems/{system_id}").status_code == 404
    assert anon.get(f"/v1/public/shared/{token}").status_code == 404
    owner.close()


@pytest.mark.public_profiles
def test_a_fresh_account_has_profile_visibility_armed_by_default():
    """DECISION 1: the category is on out of the box, but with no grace window -
    armed means 're-auth first', not 'wait a week'."""
    owner = _register()
    settings = owner.get("/v1/system/safety").json()["settings"]
    assert settings["applies_to_profile_visibility"] is True
    assert settings["grace_period_days"] == 0
    owner.close()


@pytest.mark.public_profiles
def test_going_public_again_brings_the_profile_straight_back_by_default():
    """Suppression is not revocation: the grants were never touched, so there
    is nothing to republish. The category is armed by default, but at the `none`
    auth tier the step-up verifies nothing and the grace window is 0, so a bare
    flip back to public restores the page with no re-auth and no wait."""
    owner = _register()
    system_id, token, _ = _both_grant_urls(owner)
    owner.patch("/v1/systems/me", json={"privacy": "private"})
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 404

    r = owner.patch("/v1/systems/me", json={"privacy": "public"})
    assert r.status_code == 200, r.text
    assert r.json()["pending_privacy"] is None
    anon = _anon()
    assert anon.get(f"/v1/public/systems/{system_id}").status_code == 200
    assert anon.get(f"/v1/public/shared/{token}").status_code == 200
    owner.close()


@pytest.mark.public_profiles
def test_republishing_demands_step_up_when_a_tier_is_armed():
    """DECISION 2, the immediate arm: with an auth tier on the category, raising
    system privacy to public is an exposure and demands re-auth first. Grace is
    0, so the correct password republishes at once - no staging."""
    owner = _register()
    system_id, token, _ = _both_grant_urls(owner)
    _arm_visibility_stepup(owner)  # password tier, grace 0
    owner.patch("/v1/systems/me", json={"privacy": "private"})

    # A bare flip is refused, and the surface stays dark.
    bare = owner.patch("/v1/systems/me", json={"privacy": "public"})
    assert bare.status_code == 400, bare.text
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 404

    # Wrong password is refused too.
    wrong = owner.patch(
        "/v1/systems/me", json={"privacy": "public", "password": "wrongpass"}
    )
    assert wrong.status_code == 403, wrong.text
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 404

    # Correct password republishes immediately (grace 0, nothing staged).
    ok = owner.patch(
        "/v1/systems/me",
        json={"privacy": "public", "password": "testpassword123"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["privacy"] == "public"
    assert ok.json()["pending_privacy"] is None
    anon = _anon()
    assert anon.get(f"/v1/public/systems/{system_id}").status_code == 200
    assert anon.get(f"/v1/public/shared/{token}").status_code == 200
    owner.close()


@pytest.mark.public_profiles
def test_republishing_with_a_grace_window_stages_then_the_sweep_promotes(
    admin_client: httpx.Client,
):
    """DECISION 2, the staged arm: category on + grace > 0. The re-authed raise
    is accepted but parks pending; the surface stays dark until the finalize
    sweep promotes it, exactly like a member/group/edge raise."""
    owner = _register()
    system_id, token, _ = _both_grant_urls(owner)
    _arm_visibility_stepup(owner, grace=7)
    owner.patch("/v1/systems/me", json={"privacy": "private"})

    staged = owner.patch(
        "/v1/systems/me",
        json={"privacy": "public", "password": "testpassword123"},
    )
    assert staged.status_code == 200, staged.text
    body = staged.json()
    # Live level is still not public; the raise is staged.
    assert body["privacy"] != "public"
    assert body["pending_privacy"] == "public"
    assert body["privacy_activates_at"] is not None
    # Still dark inside the window.
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 404

    _backdate_system_privacy(system_id)
    assert (
        admin_client.post(
            "/v1/admin/jobs/finalize_share_activations/run"
        ).status_code
        == 200
    )

    promoted = owner.get("/v1/systems/me").json()
    assert promoted["privacy"] == "public"
    assert promoted["pending_privacy"] is None
    assert promoted["privacy_activates_at"] is None
    anon = _anon()
    assert anon.get(f"/v1/public/systems/{system_id}").status_code == 200
    assert anon.get(f"/v1/public/shared/{token}").status_code == 200
    owner.close()


@pytest.mark.public_profiles
def test_lowering_system_privacy_is_always_instant_and_ungated():
    """Un-exposing never waits and never re-auths, even with the tier and a
    grace window armed - and it cancels any staged raise."""
    owner = _register()
    system_id, _, _ = _both_grant_urls(owner)
    _arm_visibility_stepup(owner, grace=7)

    # Going dark: no password, no window.
    down = owner.patch("/v1/systems/me", json={"privacy": "private"})
    assert down.status_code == 200, down.text
    assert down.json()["privacy"] == "private"
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 404

    # Stage a raise, then lower again: the staged raise is cancelled outright.
    owner.patch(
        "/v1/systems/me",
        json={"privacy": "public", "password": "testpassword123"},
    )
    cancelled = owner.patch("/v1/systems/me", json={"privacy": "friends"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["privacy"] == "friends"
    assert cancelled.json()["pending_privacy"] is None
    assert cancelled.json()["privacy_activates_at"] is None
    owner.close()


@pytest.mark.public_profiles
def test_system_privacy_raise_is_ungated_without_a_grant():
    """Nothing to reveal, nothing to gate: with no grant on the system, going
    public is instant even with the tier and window armed - the sibling of the
    member/group/edge 'raise exposes nothing' cases."""
    owner = _register()
    _go_public(owner)  # no grant created; profile_serving has nothing to serve
    owner.patch("/v1/systems/me", json={"privacy": "private"})
    _arm_visibility_stepup(owner, grace=7)

    r = owner.patch("/v1/systems/me", json={"privacy": "public"})
    assert r.status_code == 200, r.text
    assert r.json()["privacy"] == "public"
    assert r.json()["pending_privacy"] is None
    owner.close()


@pytest.mark.public_profiles
@pytest.mark.parametrize("state", ["suspended", "banned", "pending_deletion"])
def test_account_state_suppresses_both_grant_kinds(state):
    owner, email = _registered()
    system_id, token, _ = _both_grant_urls(owner)
    anon = _anon()
    assert anon.get(f"/v1/public/systems/{system_id}").status_code == 200

    _set_account_status(email, state)

    for path in (f"/v1/public/systems/{system_id}", f"/v1/public/shared/{token}"):
        r = anon.get(path)
        assert r.status_code == 404
        # Identical to every other reason a page is not there. A public
        # endpoint must never report moderation state about a stranger.
        assert r.json()["detail"] == "Not found"
    owner.close()


@pytest.mark.public_profiles
def test_the_page_returns_when_the_account_does():
    """The chosen semantic, stated as a test: suspension is temporary, the
    grants survive it, and lifting it restores the page with no further act
    from the owner."""
    owner, email = _registered()
    system_id, token, _ = _both_grant_urls(owner)

    _set_account_status(email, "suspended")
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 404

    _set_account_status(email, "active")
    anon = _anon()
    assert anon.get(f"/v1/public/systems/{system_id}").status_code == 200
    assert anon.get(f"/v1/public/shared/{token}").status_code == 200
    owner.close()


# ---------------------------------------------------------------------------
# Archived and deletion-queued members leave the surface immediately
# ---------------------------------------------------------------------------


def _arm_member_delete_safety(c: httpx.Client) -> None:
    """Grace window on member deletion, with no re-auth tier, so a delete
    queues instead of executing and the test does not need a password."""
    r = c.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
            "auth_tier": "none",
        },
    )
    assert r.status_code == 200, r.text


def _full_surface(owner: httpx.Client) -> tuple[str, str, str, str]:
    """A profile serving every surface a member can appear on at once.

    Returns (system_id, member_a, member_b, group_id). Two members joined by a
    published edge and both in a public group, so one call sets up the roster,
    the count, the fronting card, the edge endpoints and the group roster - the
    five places a member's name can reach a visitor.
    """
    a = _member(owner, "StaysA")
    b = _member(owner, "GoesB")
    rtype = _rel_type(owner, f"Pal-{uuid.uuid4().hex[:6]}")
    _edge(owner, a, b, rtype)
    group = owner.post(
        "/v1/groups", json={"name": f"G-{uuid.uuid4().hex[:6]}", "privacy": "public"}
    ).json()["id"]
    owner.put(f"/v1/groups/{group}/members", json={"member_ids": [a, b]})
    system_id, _ = _published_system(
        owner,
        members=[a, b],
        include_fronting=True,
        include_relationships=True,
        include_groups=True,
        member_permalinks=True,
    )
    owner.post("/v1/fronts", json={"member_ids": [a, b]})
    return system_id, a, b, group


def _surface_snapshot(system_id: str, group_id: str) -> dict:
    anon = _anon()
    base = f"/v1/public/systems/{system_id}"
    groups = anon.get(f"{base}/groups").json()["groups"]
    roster = next(g["members"] for g in groups if g["id"] == group_id)
    fronting = anon.get(f"{base}/fronting").json()
    return {
        "members": [m["id"] for m in anon.get(f"{base}/members").json()],
        "count": anon.get(base).json()["member_count"],
        "fronting": [m["id"] for m in fronting["members"]],
        "hidden_count": fronting["hidden_count"],
        "edges": anon.get(f"{base}/relationships").json()["relationships"],
        "group_roster": [m["id"] for m in roster],
    }


@pytest.mark.public_profiles
def test_archiving_a_member_removes_them_from_every_public_surface():
    owner = _register()
    system_id, a, b, group = _full_surface(owner)

    before = _surface_snapshot(system_id, group)
    assert set(before["members"]) == {a, b}
    assert before["count"] == 2
    assert set(before["fronting"]) == {a, b}
    assert len(before["edges"]) == 1
    assert set(before["group_roster"]) == {a, b}

    assert owner.post(f"/v1/members/{b}/archive").status_code == 200

    after = _surface_snapshot(system_id, group)
    assert after["members"] == [a]
    assert after["count"] == 1
    assert after["fronting"] == [a]
    # The edge composes out of the member id set, so it goes with its endpoint
    # rather than being separately filtered.
    assert after["edges"] == []
    assert after["group_roster"] == [a]
    # And not as an anonymous presence bit either: an archived member must not
    # register as "somebody else is fronting".
    assert after["hidden_count"] == 0
    # Their permalink goes too, with the same 404 as a member who was never in
    # the view at all.
    assert (
        _anon().get(f"/v1/public/systems/{system_id}/members/{b}").status_code == 404
    )
    owner.close()


@pytest.mark.public_profiles
def test_unarchiving_restores_a_member_without_re_staging_them():
    """Coming back is not a new exposure decision: archiving never touched the
    view membership, so there is no grace window to serve a second time."""
    owner = _register()
    system_id, a, b, group = _full_surface(owner)
    _arm_visibility_safety(owner)

    owner.post(f"/v1/members/{b}/archive")
    assert _surface_snapshot(system_id, group)["members"] == [a]

    assert owner.post(f"/v1/members/{b}/unarchive").status_code == 200

    restored = _surface_snapshot(system_id, group)
    assert set(restored["members"]) == {a, b}
    assert restored["count"] == 2
    assert set(restored["fronting"]) == {a, b}
    assert len(restored["edges"]) == 1
    assert set(restored["group_roster"]) == {a, b}
    owner.close()


@pytest.mark.public_profiles
def test_a_member_queued_for_deletion_stops_being_published_at_once():
    """The grace window exists so the owner can undo, not so the world gets a
    last look at somebody they have asked to remove."""
    owner = _register()
    system_id, a, b, group = _full_surface(owner)
    _arm_member_delete_safety(owner)

    queued = owner.delete(f"/v1/members/{b}")
    assert queued.status_code == 202, queued.text
    # The row itself is still there - this is the whole point of the window.
    assert owner.get(f"/v1/members/{b}").status_code == 200

    after = _surface_snapshot(system_id, group)
    assert after["members"] == [a]
    assert after["count"] == 1
    assert after["fronting"] == [a]
    assert after["hidden_count"] == 0
    assert after["edges"] == []
    assert after["group_roster"] == [a]
    assert (
        _anon().get(f"/v1/public/systems/{system_id}/members/{b}").status_code == 404
    )
    owner.close()


@pytest.mark.public_profiles
def test_cancelling_a_queued_member_deletion_brings_them_back():
    owner = _register()
    system_id, a, b, group = _full_surface(owner)
    _arm_member_delete_safety(owner)

    pending_id = owner.delete(f"/v1/members/{b}").json()["pending_action_id"]
    assert _surface_snapshot(system_id, group)["members"] == [a]

    cancelled = owner.delete(f"/v1/system/safety/pending-actions/{pending_id}")
    assert cancelled.status_code == 204, cancelled.text
    assert set(_surface_snapshot(system_id, group)["members"]) == {a, b}
    owner.close()


@pytest.mark.public_profiles
def test_a_group_queued_for_deletion_stops_being_published_at_once():
    owner = _register()
    m = _member(owner, "InGroup")
    group = owner.post(
        "/v1/groups", json={"name": "Doomed", "privacy": "public"}
    ).json()["id"]
    owner.put(f"/v1/groups/{group}/members", json={"member_ids": [m]})
    system_id, _ = _published_system(owner, members=[m], include_groups=True)
    r = owner.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_groups": True,
            "auth_tier": "none",
        },
    )
    assert r.status_code == 200, r.text

    base = f"/v1/public/systems/{system_id}"
    assert len(_anon().get(f"{base}/groups").json()["groups"]) == 1

    queued = owner.delete(f"/v1/groups/{group}")
    assert queued.status_code == 202, queued.text
    assert _anon().get(f"{base}/groups").json()["groups"] == []
    owner.close()


@pytest.mark.public_profiles
def test_a_field_queued_for_deletion_stops_being_served_at_once():
    owner = _register()
    m = _member(owner, "HasField")
    field = owner.post(
        "/v1/fields",
        json={"name": "Doomed", "field_type": "text", "privacy": "public"},
    ).json()["id"]
    assert owner.put(
        f"/v1/members/{m}/fields", json=[{"field_id": field, "value": "x"}]
    ).status_code == 200
    system_id, view = _published_system(owner, members=[m])
    assert owner.post(
        f"/v1/share-views/{view}/fields", json={"field_id": field}
    ).status_code == 200
    r = owner.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_fields": True,
            "auth_tier": "none",
        },
    )
    assert r.status_code == 200, r.text

    base = f"/v1/public/systems/{system_id}"
    assert _anon().get(f"{base}/members").json()[0]["fields"] == {"Doomed": "x"}

    queued = owner.delete(f"/v1/fields/{field}")
    assert queued.status_code == 202, queued.text
    assert _anon().get(f"{base}/members").json()[0]["fields"] == {}
    owner.close()


# ---------------------------------------------------------------------------
# Owner preview - the same payload, before anyone else gets it
# ---------------------------------------------------------------------------


def _shape(value):
    """A payload reduced to its structure: key sets all the way down, with leaf
    VALUES replaced by their type name.

    Values are dropped on purpose. What has to match between the preview and
    the real thing is the shape - so a field that drifts into one surface and
    not the other fails this - while the values themselves include signed URLs
    whose expiry moves between two requests a moment apart.
    """
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(v) for v in value]
    return type(value).__name__


@pytest.mark.public_profiles
def test_preview_matches_the_anonymous_endpoints_section_by_section():
    """The drift test, and the reason the preview goes through the projection
    instead of imitating it in the client. Every section is compared against
    the endpoint an actual visitor hits, on a view with everything turned on."""
    owner = _register()
    a = _member(owner, "PreviewA", pronouns="they/them")
    b = _member(owner, "PreviewB")
    field = owner.post(
        "/v1/fields",
        json={"name": "Role", "field_type": "text", "privacy": "public"},
    ).json()["id"]
    assert owner.put(
        f"/v1/members/{a}/fields", json=[{"field_id": field, "value": "cook"}]
    ).status_code == 200

    system_id, view = _published_system(
        owner,
        members=[a, b],
        include_bio=True,
        include_fronting=True,
        include_relationships=True,
        include_groups=True,
        member_permalinks=True,
    )
    assert owner.post(
        f"/v1/share-views/{view}/fields", json={"field_id": field}
    ).status_code == 200
    _publish_group(owner, _group(owner, "Kitchen", members=[a, b]))
    _edge(owner, a, b, _rel_type(owner, "Partners"))
    assert owner.post("/v1/fronts", json={"member_ids": [a]}).status_code == 201

    preview = owner.get(f"/v1/share-views/{view}/preview")
    assert preview.status_code == 200, preview.text
    got = preview.json()

    anon = _anon()
    base = f"/v1/public/systems/{system_id}"
    for section, path in (
        ("system", ""),
        ("members", "/members"),
        ("fronting", "/fronting"),
        ("relationships", "/relationships"),
        ("groups", "/groups"),
    ):
        real = anon.get(f"{base}{path}")
        assert real.status_code == 200, (section, real.text)
        assert _shape(got[section]) == _shape(real.json()), section

    # Nothing owner-only rides along beside the sections. `suppressed` is the
    # only addition, and it is about the page rather than in it.
    assert set(got) == {
        "system",
        "members",
        "fronting",
        "relationships",
        "groups",
        "suppressed",
    }
    assert got["suppressed"] is None
    # Sanity that the comparison above was not comparing two empty pages.
    assert len(got["members"]) == 2
    assert got["members"][0]["fields"] == {"Role": "cook"}
    assert [m["name"] for m in got["fronting"]["members"]] == ["PreviewA"]
    assert len(got["relationships"]["relationships"]) == 1
    assert [g["name"] for g in got["groups"]["groups"]] == ["Kitchen"]
    owner.close()


@pytest.mark.public_profiles
def test_preview_nulls_the_sections_the_view_does_not_serve():
    """Null, not empty. Empty is a thing a served section can legitimately be,
    and the owner has to be able to tell the two apart - it is the difference
    between "nobody is fronting" and "visitors cannot see who is fronting"."""
    owner = _register()
    m = _member(owner, "Solo")
    _, view = _published_system(owner, members=[m], include_members=False)

    got = owner.get(f"/v1/share-views/{view}/preview").json()
    assert got["members"] is None
    assert got["fronting"] is None
    assert got["relationships"] is None
    assert got["groups"] is None
    # The system section is always served, and reports the roster's absence the
    # same way the public endpoint does: a null count, never a zero.
    assert got["system"]["member_count"] is None
    owner.close()


@pytest.mark.public_profiles
def test_preview_works_on_a_view_nothing_points_at():
    """Previewing something UNPUBLISHED is the whole point: an owner should be
    able to look before they publish, not only after."""
    owner = _register()
    m = _member(owner, "NotYetPublic")
    _go_public(owner)
    view = owner.post(
        "/v1/share-views", json={"name": "Draft", "include_members": True}
    ).json()["id"]
    assert owner.post(
        f"/v1/share-views/{view}/members", json={"member_id": m}
    ).status_code == 200

    r = owner.get(f"/v1/share-views/{view}/preview")
    assert r.status_code == 200, r.text
    got = r.json()
    assert [c["name"] for c in got["members"]] == ["NotYetPublic"]
    assert got["suppressed"] is None
    owner.close()


@pytest.mark.public_profiles
def test_preview_says_when_nothing_would_actually_serve():
    """A preview with no suppression notice would be the most convincing lie
    this feature could tell: a healthy-looking page for something the world is
    getting a 404 for."""
    owner = _register()
    m = _member(owner, "Suppressed")
    system_id, view = _published_system(owner, members=[m])
    assert owner.get(f"/v1/share-views/{view}/preview").json()["suppressed"] is None

    assert owner.patch("/v1/systems/me", json={"privacy": "private"}).status_code == 200
    assert _anon().get(f"/v1/public/systems/{system_id}").status_code == 404

    got = owner.get(f"/v1/share-views/{view}/preview").json()
    assert got["suppressed"] == "system_private"
    # Deliberately still shows the content: the owner opened this to check what
    # the page looks like, and blanking it would answer a question they did not
    # ask while hiding the one they did.
    assert [c["name"] for c in got["members"]] == ["Suppressed"]
    owner.close()


@pytest.mark.public_profiles
def test_preview_is_not_anonymous():
    """It serves public-shaped payloads for views that may not be published at
    all, so it must be exactly as authenticated as the rest of the owner API."""
    owner = _register()
    m = _member(owner, "Private")
    _, view = _published_system(owner, members=[m])

    r = _anon().get(f"/v1/share-views/{view}/preview")
    assert r.status_code == 401, r.text

    # And another logged-in account gets the same 404 an unknown id gets - no
    # cross-tenant existence oracle, same as every other view route.
    stranger = _register()
    assert stranger.get(f"/v1/share-views/{view}/preview").status_code == 404
    stranger.close()
    owner.close()


# ---------------------------------------------------------------------------
# Public media is bound to live grant/system state (re-checked per fetch)
# ---------------------------------------------------------------------------


def _published_member_with_avatar(owner: httpx.Client) -> tuple[str, str, str]:
    """Publish a member carrying a hosted avatar. Returns
    (system_id, view_id, media_path) where media_path is the anonymous
    ``/v1/public/files/...`` URL the public payload hands out for it."""
    key = _upload(owner, "avatar")
    m = _member(owner, "Pictured", avatar_url=key)
    system_id, view = _published_system(owner, members=[m], include_members=True)
    mem = _anon().get(f"/v1/public/systems/{system_id}/members").json()[0]
    media_path = mem["avatar_url"]
    assert media_path and media_path.startswith("/v1/public/files/"), media_path
    return system_id, view, media_path


@pytest.mark.public_profiles
def test_public_media_serves_while_live():
    owner = _register()
    _, _, media_path = _published_member_with_avatar(owner)
    r = _anon().get(media_path)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    # No longer immutable, and the tail is bounded to the JSON surface's own:
    # the bytes can stop being authorized before the capability's HMAC expires.
    assert r.headers["cache-control"] == "public, max-age=60"
    owner.close()


@pytest.mark.public_profiles
def test_public_media_404s_after_grant_revoked():
    owner = _register()
    _, _, media_path = _published_member_with_avatar(owner)
    assert _anon().get(media_path).status_code == 200

    gid = next(g["id"] for g in owner.get("/v1/share-grants").json())
    assert owner.delete(f"/v1/share-grants/{gid}").status_code in (200, 204)

    # Same capability URL, still-valid HMAC: only the per-fetch re-check turns
    # it dark, in step with the JSON surface rather than up to two hours later.
    assert _anon().get(media_path).status_code == 404
    owner.close()


@pytest.mark.public_profiles
def test_public_media_404s_after_system_set_private():
    owner = _register()
    _, _, media_path = _published_member_with_avatar(owner)
    assert _anon().get(media_path).status_code == 200

    assert owner.patch("/v1/systems/me", json={"privacy": "private"}).status_code == 200
    assert _anon().get(media_path).status_code == 404
    owner.close()


@pytest.mark.public_profiles
def test_public_media_404s_after_account_suspended():
    owner = _register()
    _, _, media_path = _published_member_with_avatar(owner)
    assert _anon().get(media_path).status_code == 200

    uid = owner.get("/v1/auth/me").json()["id"]

    async def _suspend(db) -> None:
        from sheaf.models.user import AccountStatus, User

        user = await db.get(User, uuid.UUID(uid))
        assert user is not None
        user.account_status = AccountStatus.SUSPENDED
        user.suspended_until = datetime.now(UTC) + timedelta(days=1)

    _in_db(_suspend)

    assert _anon().get(media_path).status_code == 404
    owner.close()
