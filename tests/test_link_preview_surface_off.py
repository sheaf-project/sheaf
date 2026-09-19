"""Link previews with the instance's public surface switched OFF.

The other side of `test_link_preview_api.py`, and it needs the opposite setting,
hence its own file and marker (`PUBLIC_PROFILES_ENABLED=false`).

Two things to pin. The preview route must go generic wholesale, for the same
reason the anonymous JSON router 404s wholesale: with the surface off the page
behind the card does not exist, so a card describing it would be advertising
something nobody can open. And turning the setting ON must be refused while the
switch is off, exactly as the sibling exposure flags are - a rich card configured
now is one that would start unfurling the day an operator flips the surface back
on, with nobody around who agreed to it.
"""

import asyncio
import os
import re
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8001")

pytestmark = pytest.mark.public_profiles_off

GENERIC_MARKER = "A shared system profile"


def _anon() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL)


def _system_id(c: httpx.Client) -> str:
    r = c.get("/v1/systems/me")
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _in_db(work) -> None:
    """Run `work(db)` straight against the test database, then commit.

    Same helper as the sibling privacy suites keep locally; see
    `test_sharing_api.py`.
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


def _dormant_view(c: httpx.Client, name: str) -> str:
    """A share view that exists while the public surface is off.

    Creating one through the API is refused here, because creating a view is
    itself a loosening - which is correct, and is pinned elsewhere. But it also
    means a test that asks the API for a view gets a 403 and then tests nothing
    about views at all. Both tests below did exactly that: one returned early
    before reaching the refusal it is named for, and the other skipped outright,
    so the rule that going dark is never blocked had a test that looked like a
    guard and asserted nothing.

    So the view is seeded the way `_dormant_setup` in `test_sharing_api.py`
    seeds its own, and for the same reason: these tests describe the dormant
    state left behind when an operator switches the surface off, not a fresh
    publish.
    """
    system_id = _system_id(c)
    view_id = uuid.uuid4()

    async def _work(db) -> None:
        from sheaf.models.share import ShareView

        db.add(
            ShareView(
                id=view_id,
                system_id=uuid.UUID(system_id),
                name=name,
                member_permalinks=True,
            )
        )

    _in_db(_work)
    return str(view_id)


def test_preview_is_generic_while_the_surface_is_off(auth_client: httpx.Client):
    """Whatever exists underneath, the card says nothing while the surface is off."""
    _set = auth_client.patch("/v1/systems/me", json={"name": "Dormant System"})
    assert _set.status_code == 200, _set.text
    system_id = _system_id(auth_client)

    with _anon() as anon:
        page = anon.get(f"/v1/link-preview/p/{system_id}")
        missing = anon.get(f"/v1/link-preview/p/{uuid.uuid4()}")
    assert page.status_code == 200
    assert GENERIC_MARKER in page.text
    assert "Dormant System" not in page.text

    # Still no oracle: a real system and an invented one give the same card.
    # og:url echoes the path the requester asked for, so it differs by
    # construction and is excluded - the requester already knows their own URL.
    def card(resp: httpx.Response) -> str:
        return re.sub(r'\s*<meta property="og:url"[^>]*/>\n', "", resp.text)

    assert card(page) == card(missing)


def test_share_link_preview_is_generic_too(auth_client: httpx.Client):
    with _anon() as anon:
        page = anon.get("/v1/link-preview/s/some-made-up-token")
    assert page.status_code == 200
    assert GENERIC_MARKER in page.text


def test_turning_the_setting_on_is_refused_while_the_surface_is_off(
    auth_client: httpx.Client,
):
    """A loosening is refused; both modes are EXPOSURE_FLAGS, so this comes free.

    The view is seeded rather than requested, because the API refuses to create
    one at all with the surface off. That refusal is a stricter version of the
    same rule and is pinned in the sharing suite; accepting it here instead
    meant this test returned before reaching the raise it is named for.
    """
    vid = _dormant_view(auth_client, f"Off-{uuid.uuid4().hex[:6]}")
    for field in ("link_preview_mode", "member_link_preview_mode"):
        r = auth_client.patch(
            f"/v1/share-views/{vid}", json={field: "system_details"}
        )
        assert r.status_code == 403, (field, r.text)
        assert (
            auth_client.get(f"/v1/share-views/{vid}").json()[field] == "generic"
        )


def test_turning_the_setting_off_still_works_while_the_surface_is_off(
    auth_client: httpx.Client,
):
    """Nothing may slow down going dark, including the operator's own switch.

    The interesting case is a view left behind with a rich mode already set,
    from before the surface went off. Lowering it back to generic must still
    work, or an operator flipping the switch would have trapped the owner with
    an exposure setting they can no longer withdraw.
    """
    vid = _dormant_view(auth_client, f"Off2-{uuid.uuid4().hex[:6]}")

    async def _arm(db) -> None:
        from sheaf.models.share import ShareView

        view = await db.get(ShareView, uuid.UUID(vid))
        assert view is not None
        view.link_preview_mode = "system_details"
        view.member_link_preview_mode = "system_details"

    _in_db(_arm)

    for field in ("link_preview_mode", "member_link_preview_mode"):
        r = auth_client.patch(f"/v1/share-views/{vid}", json={field: "generic"})
        assert r.status_code == 200, (field, r.text)
        assert auth_client.get(f"/v1/share-views/{vid}").json()[field] == "generic"
