"""HTTP-level tests for the anonymous public-profile surface.

The `public_profiles` mark routes these to the run_tests.sh config that runs the
app with PUBLIC_PROFILES_ENABLED=true (the surface is off by default). The
`test_disabled_*` cases are deliberately UNMARKED so they run against the normal
configs, where the feature is off, and prove the router 404s wholesale.

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
# Feature disabled (UNMARKED: runs where PUBLIC_PROFILES_ENABLED is off)
# ---------------------------------------------------------------------------


def test_disabled_public_system_404s(client: httpx.Client):
    r = client.get(f"/v1/public/systems/{uuid.uuid4()}")
    assert r.status_code == 404


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
    }
    assert body["member_count"] == 1
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
        "id", "name", "display_name", "pronouns", "avatar_url", "banner_url",
        "color", "bio", "fields",
    }
    # note / privacy / birthday / created_at etc. must never appear.
    assert "note" not in members[0]
    assert "privacy" not in members[0]
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
    # Expose only the Role field through the view.
    owner.post(f"/v1/share-views/{view}/fields", json={"field_id": shown_field["id"]})
    system_id = owner.get("/v1/systems/me").json()["id"]

    mem = _anon().get(f"/v1/public/systems/{system_id}/members").json()[0]
    assert mem["fields"] == {"Role": "Protector"}
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
        "id", "name", "display_name", "pronouns", "avatar_url", "color", "since",
    }
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
    exist to prevent."""
    owner = _register()
    a, b = _member(owner, "EdgeA"), _member(owner, "EdgeB")
    system_id, _ = _published_system(
        owner,
        members=[a, b],
        include_members=False,
        include_relationships=True,
    )
    _edge(owner, a, b, _rel_type(owner, "Partner"))

    body = _anon().get(f"/v1/public/systems/{system_id}/relationships").json()
    assert body["relationships"] == []
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
