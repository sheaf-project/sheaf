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


def _anon() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL)


def _published_system(
    c: httpx.Client,
    *,
    members: list[str] | None = None,
    include_bio: bool = False,
    include_fronting: bool = False,
    fronting_show_count: bool = True,
) -> tuple[str, str]:
    """Create a view with the given members, publish it publicly, and return
    (system_id, view_id)."""
    c.post("/v1/auth/me/attest-adult")
    view = c.post(
        "/v1/share-views",
        json={
            "name": f"V-{uuid.uuid4().hex[:6]}",
            "include_bio": include_bio,
            "include_fronting": include_fronting,
            "fronting_show_count": fronting_show_count,
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


def _link_token(c: httpx.Client, view_id: str) -> str:
    r = c.post("/v1/share-grants", json={"view_id": view_id, "subject_type": "link"})
    assert r.status_code == 201, r.text
    return r.json()["token"]


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
