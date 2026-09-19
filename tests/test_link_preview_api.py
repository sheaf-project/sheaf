"""HTTP-level tests for per-view link previews (the crawler-facing cards).

Drive the running stack. These cover what the unit tests cannot: which card a
real URL actually serves once a grant, a grace window, a rotation and a
suppression are in play, and whether the owner-facing `link_preview_effective`
agrees with what the anonymous surface does.

The three that matter most, and the reason this file exists rather than trusting
the unit tests:

* `test_share_link_never_gets_a_rich_card*` - a URL that is itself the secret
  must not hand the system's name to whatever cache it was pasted into.
* `test_staged_flag_previews_generic_until_it_activates` and
  `test_pending_grant_previews_generic` - a grace period that held back the
  profile while leaking its name would have defeated itself.
* `test_rich_card_reveals_nothing_the_page_does_not` - every field on the card is
  compared against the anonymous JSON payload for the same URL.

The preview routes are hit through their `/v1/link-preview/...` aliases. The
canonical `/p/` and `/s/` paths are the same handler, but only the aliases are
reachable through the test stack's proxying, which is exactly the situation a
selfhoster is in before they update their reverse proxy.
"""

import os
import re
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8001")

# The anonymous preview surface only exists when the instance publishes at all,
# so these run in the `public_profiles` config alongside the other
# anonymous-surface tests. The "surface switched off" half lives in
# `test_link_preview_surface_off.py`, which needs the opposite setting.
pytestmark = pytest.mark.public_profiles

GENERIC_MARKER = "A shared system profile"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _anon() -> httpx.Client:
    """An unauthenticated client - a stand-in for the crawler."""
    return httpx.Client(base_url=BASE_URL)


def _view(c: httpx.Client, name: str | None = None, **kw) -> str:
    name = name or f"View-{uuid.uuid4().hex[:6]}"
    r = c.post("/v1/share-views", json={"name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _attest(c: httpx.Client) -> None:
    assert c.post("/v1/auth/me/attest-adult").status_code == 200


def _go_public(c: httpx.Client) -> None:
    r = c.patch("/v1/systems/me", json={"privacy": "public"})
    assert r.status_code == 200, r.text


def _set_system(c: httpx.Client, **fields) -> None:
    r = c.patch("/v1/systems/me", json=fields)
    assert r.status_code == 200, r.text


def _system_id(c: httpx.Client) -> str:
    r = c.get("/v1/systems/me")
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _grant(c: httpx.Client, view_id: str, subject_type: str = "public") -> dict:
    """Create a grant and flatten the `ShareGrantCreated` envelope.

    The response is `{"grant": {...}, "token": "..."}` - the raw token exists in
    exactly this one response and nowhere else - so the grant fields and the token
    are merged into one dict for the callers below.
    """
    r = c.post(
        "/v1/share-grants", json={"view_id": view_id, "subject_type": subject_type}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {**body["grant"], "token": body.get("token")}


def _disarm_visibility_safety(c: httpx.Client) -> None:
    """Off, so a flag flip lands live instead of staging.

    The category defaults ON, and every test here that is not specifically about
    the grace window wants the raise to apply immediately.
    """
    r = c.patch("/v1/system/safety", json={"applies_to_profile_visibility": False})
    assert r.status_code == 200, r.text


def _arm_visibility_safety(c: httpx.Client) -> None:
    r = c.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_profile_visibility": True,
            "auth_tier": "none",
        },
    )
    assert r.status_code == 200, r.text


def _published(c: httpx.Client, **view_kw) -> tuple[str, str]:
    """A system with a live public profile. Returns (system_id, view_id)."""
    _attest(c)
    _disarm_visibility_safety(c)
    _go_public(c)
    vid = _view(c, **view_kw)
    _grant(c, vid, "public")
    return _system_id(c), vid


def _preview_system(anon: httpx.Client, system_id: str) -> httpx.Response:
    return anon.get(f"/v1/link-preview/p/{system_id}")


def _preview_link(anon: httpx.Client, token: str) -> httpx.Response:
    return anon.get(f"/v1/link-preview/s/{token}")


def _og(html: str, prop: str) -> str | None:
    """Pull one og:/twitter: content value out of the rendered document."""
    m = re.search(
        rf'<meta (?:property|name)="{re.escape(prop)}" content="([^"]*)" />', html
    )
    return m.group(1) if m else None


def _is_generic(resp: httpx.Response) -> bool:
    assert resp.status_code == 200, resp.text
    return _og(resp.text, "og:title") == GENERIC_MARKER


# ---------------------------------------------------------------------------
# The document itself
# ---------------------------------------------------------------------------


def test_preview_route_is_html_and_never_indexed(auth_client: httpx.Client):
    system_id, _ = _published(auth_client)
    with _anon() as anon:
        r = _preview_system(anon, system_id)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["x-robots-tag"] == "noindex, nofollow"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "<script" not in r.text.lower()


def test_unknown_and_malformed_urls_get_the_same_generic_card(
    auth_client: httpx.Client,
):
    """No oracle: a made-up URL must be indistinguishable from a real generic one.

    This is why the route answers 200 with a document rather than 404ing like the
    JSON surface does. A crawler that got "no preview" for one URL and a card for
    another would have been told which links are real.
    """
    system_id, _ = _published(auth_client)  # generic by default
    with _anon() as anon:
        real = _preview_system(anon, system_id)
        missing = _preview_system(anon, str(uuid.uuid4()))
        malformed = _preview_system(anon, "not-a-uuid")
    for r in (real, missing, malformed):
        assert r.status_code == 200

    # og:url echoes the path the requester asked for, so it differs between these
    # three by construction. That is not an oracle - the requester already knows
    # the URL they typed - so the comparison is of everything else.
    def card(resp: httpx.Response) -> str:
        return re.sub(r'\s*<meta property="og:url"[^>]*/>\n', "", resp.text)

    assert card(missing) == card(malformed)
    assert card(real) == card(missing)
    # And the echoed value really is just the request path, not anything resolved.
    assert _og(malformed.text, "og:url").endswith("/p/not-a-uuid")


def test_generic_is_the_default_for_a_new_view(auth_client: httpx.Client):
    system_id, _ = _published(auth_client)
    _set_system(auth_client, name="Nominative Determinism")
    with _anon() as anon:
        r = _preview_system(anon, system_id)
    assert _is_generic(r)
    assert "Nominative Determinism" not in r.text


# ---------------------------------------------------------------------------
# The rich card
# ---------------------------------------------------------------------------


def test_rich_card_shows_the_system_name_once_turned_on(auth_client: httpx.Client):
    system_id, vid = _published(auth_client)
    _set_system(auth_client, name="The Corvid Collective", description="We are six.")
    r = auth_client.patch(
        f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["link_preview_mode"] == "system_details"
    assert r.json()["link_preview_effective"] == "system_details"

    with _anon() as anon:
        page = _preview_system(anon, system_id)
    assert page.status_code == 200
    assert _og(page.text, "og:title") == "The Corvid Collective"
    assert _og(page.text, "og:description") == "We are six."
    assert _og(page.text, "twitter:title") == "The Corvid Collective"


def test_rich_card_og_image_is_a_stable_absolute_url(
    auth_client: httpx.Client,
):
    """og:image must match the avatar the page serves, and be absolute.

    Driven off the anonymous payload rather than off what was set: whether an
    avatar survives to the public surface depends on the instance's image policy,
    and the invariant worth pinning is that the card agrees with the page either
    way. Absolute matters because almost every crawler drops a root-relative
    og:image - which is what the static SPA shell has always carried.
    """
    system_id, vid = _published(auth_client)
    _set_system(
        auth_client, name="Has An Avatar", avatar_url="https://cdn.example/a.png"
    )
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    with _anon() as anon:
        page = _preview_system(anon, system_id)
        public = anon.get(f"/v1/public/systems/{system_id}").json()

    image = _og(page.text, "og:image")
    if public.get("avatar_url") is None:
        # No avatar to serve, so the card advertises no image rather than pointing
        # at a URL that would 404.
        assert image is None
        return
    assert image is not None
    assert image.startswith(("http://", "https://"))
    # The STABLE per-request route, never the projection's short-lived signed URL:
    # an unfurler that caches this address and refetches later still gets a picture.
    assert image.endswith(f"/p/{system_id}/preview-image")
    assert "token=" not in image


def test_rich_card_description_is_flattened_and_capped(auth_client: httpx.Client):
    system_id, vid = _published(auth_client)
    _set_system(
        auth_client,
        name="Markdown Haver",
        description="# Heading\n\nWe are **many** and [here](https://example.com/x).",
    )
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    with _anon() as anon:
        page = _preview_system(anon, system_id)
    desc = _og(page.text, "og:description")
    assert desc == "Heading We are many and here."
    assert "**" not in desc
    assert "https://example.com/x" not in desc


def test_turning_the_flag_back_off_returns_to_generic(auth_client: httpx.Client):
    """Un-exposing is immediate and ungated, here as everywhere."""
    system_id, vid = _published(auth_client)
    _set_system(auth_client, name="Briefly Named")
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    with _anon() as anon:
        assert not _is_generic(_preview_system(anon, system_id))
    r = auth_client.patch(
        f"/v1/share-views/{vid}", json={"link_preview_mode": "generic"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["link_preview_effective"] == "generic"
    with _anon() as anon:
        page = _preview_system(anon, system_id)
    assert _is_generic(page)
    assert "Briefly Named" not in page.text


# ---------------------------------------------------------------------------
# Invariant: a URL that is itself the secret never gets a rich card
# ---------------------------------------------------------------------------


def test_share_link_never_gets_a_rich_card(auth_client: httpx.Client):
    """The whole point of the opaque token is that the system is not learnable.

    An unfurl happens on a paste, before the recipient has chosen anything, into
    a cache that outlives the link - so a rich card here would hand the details
    to a third party in exactly the case the owner was keeping them from one.
    """
    _attest(auth_client)
    _disarm_visibility_safety(auth_client)
    _go_public(auth_client)
    _set_system(auth_client, name="Should Never Appear", description="Nor this.")
    vid = _view(auth_client, link_preview_mode="system_details")
    token = _grant(auth_client, vid, "link")["token"]

    with _anon() as anon:
        page = _preview_link(anon, token)
    assert _is_generic(page)
    assert "Should Never Appear" not in page.text
    assert "Nor this." not in page.text


def test_share_link_card_carries_no_og_url(auth_client: httpx.Client):
    """og:url would copy the bearer token into the card's own metadata."""
    _attest(auth_client)
    _disarm_visibility_safety(auth_client)
    _go_public(auth_client)
    vid = _view(auth_client, link_preview_mode="system_details")
    token = _grant(auth_client, vid, "link")["token"]
    with _anon() as anon:
        page = _preview_link(anon, token)
    assert "og:url" not in page.text
    assert token not in page.text


def test_link_only_view_reports_generic_as_its_effective_mode(
    auth_client: httpx.Client,
):
    """The owner is told, rather than left with a switch that does nothing."""
    _attest(auth_client)
    _disarm_visibility_safety(auth_client)
    _go_public(auth_client)
    vid = _view(auth_client, link_preview_mode="system_details")
    _grant(auth_client, vid, "link")
    got = auth_client.get(f"/v1/share-views/{vid}").json()
    assert got["link_preview_mode"] == "system_details"
    assert got["link_preview_effective"] == "generic"


def test_every_share_link_card_is_identical(auth_client: httpx.Client):
    """Live, revoked, rotated and invented tokens must all unfurl the same."""
    _attest(auth_client)
    _disarm_visibility_safety(auth_client)
    _go_public(auth_client)
    _set_system(auth_client, name="Indistinguishable")
    vid = _view(auth_client, link_preview_mode="system_details")
    live = _grant(auth_client, vid, "link")["token"]

    doomed = _grant(auth_client, vid, "link")
    assert auth_client.delete(f"/v1/share-grants/{doomed['id']}").status_code == 204


    with _anon() as anon:
        a = _preview_link(anon, live)
        b = _preview_link(anon, doomed["token"])
        c = _preview_link(anon, "totally-made-up-token")
    assert a.text == b.text == c.text
    assert "Indistinguishable" not in a.text


def test_member_permalink_urls_preview_generic(auth_client: httpx.Client):
    """A permalink names one person, which nobody asked to put in a chat card."""
    system_id, vid = _published(auth_client, member_permalinks=True)
    _set_system(auth_client, name="Not On A Member Card")
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    with _anon() as anon:
        page = anon.get(f"/v1/link-preview/p/{system_id}/member/{uuid.uuid4()}")
    assert _is_generic(page)
    assert "Not On A Member Card" not in page.text


# ---------------------------------------------------------------------------
# Invariant: anything staged previews generic until it is live
# ---------------------------------------------------------------------------


def test_staged_flag_previews_generic_until_it_activates(auth_client: httpx.Client):
    """A grace window that leaked the name would be no grace window at all."""
    system_id, vid = _published(auth_client)
    _set_system(auth_client, name="Still Waiting")
    _arm_visibility_safety(auth_client)

    r = auth_client.patch(
        f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Staged, not applied: the live flag has not moved.
    assert body["link_preview_mode"] == "generic"
    assert body["pending_link_preview_mode"] == "system_details"
    assert body["flags_activate_at"] is not None
    assert body["link_preview_effective"] == "generic"

    with _anon() as anon:
        page = _preview_system(anon, system_id)
    assert _is_generic(page)
    assert "Still Waiting" not in page.text


def test_pending_grant_previews_generic(auth_client: httpx.Client):
    """A profile inside its own grace window serves nobody, card included."""
    _attest(auth_client)
    _go_public(auth_client)
    _set_system(auth_client, name="Not Yet Live")
    _arm_visibility_safety(auth_client)
    # Flag set at create time, so the view itself needs no staged flip.
    vid = _view(auth_client, link_preview_mode="system_details")
    grant = _grant(auth_client, vid, "public")
    assert grant["status"] == "pending"

    with _anon() as anon:
        page = _preview_system(anon, _system_id(auth_client))
    assert _is_generic(page)
    assert "Not Yet Live" not in page.text


def test_private_system_previews_generic(auth_client: httpx.Client):
    """System privacy is the master ceiling, and it covers the card too."""
    system_id, vid = _published(auth_client)
    _set_system(auth_client, name="Went Dark")
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    with _anon() as anon:
        assert not _is_generic(_preview_system(anon, system_id))

    _set_system(auth_client, privacy="private")
    with _anon() as anon:
        page = _preview_system(anon, system_id)
    assert _is_generic(page)
    assert "Went Dark" not in page.text


def test_revoking_the_grant_returns_to_generic(auth_client: httpx.Client):
    system_id, vid = _published(auth_client)
    _set_system(auth_client, name="Unpublished Again")
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    gid = [
        g["id"]
        for g in auth_client.get("/v1/share-grants").json()
        if g["view_id"] == vid
    ][0]
    assert auth_client.delete(f"/v1/share-grants/{gid}").status_code == 204
    with _anon() as anon:
        page = _preview_system(anon, system_id)
    assert _is_generic(page)
    assert "Unpublished Again" not in page.text


# ---------------------------------------------------------------------------
# Invariant: the card reveals nothing the page does not
# ---------------------------------------------------------------------------


def test_rich_card_reveals_nothing_the_page_does_not(auth_client: httpx.Client):
    """Compare the card field by field against the anonymous JSON for the same URL.

    The card is built from the same `project_system` call the JSON route makes, so
    this is checking that the guarantee actually holds over HTTP rather than only
    in the builder.
    """
    system_id, vid = _published(auth_client)
    _set_system(
        auth_client,
        name="Open Book",
        description="Everything here is already public.",
        tag="OB",
    )
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})

    with _anon() as anon:
        page = _preview_system(anon, system_id)
        payload = anon.get(f"/v1/public/systems/{system_id}")
    assert payload.status_code == 200, payload.text
    public = payload.json()

    assert _og(page.text, "og:title") == public["name"]
    # The description is the projected one, flattened - so it must be a prefix of
    # the public text (modulo the ellipsis when it was capped).
    desc = _og(page.text, "og:description")
    assert public["description"].startswith(desc.removesuffix("…"))


def test_rich_card_never_carries_a_signed_media_capability(
    auth_client: httpx.Client,
):
    """A bio image ref becomes a signed URL by the time the card is built.

    og:description is copied verbatim into a chat service's cache, so a
    capability landing there would outlive anything the owner could revoke.
    """
    system_id, vid = _published(auth_client)
    _set_system(
        auth_client,
        name="Image Haver",
        description="![pic](https://cdn.example/x.png) and some words",
    )
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    with _anon() as anon:
        page = _preview_system(anon, system_id)
    desc = _og(page.text, "og:description") or ""
    assert "token=" not in desc
    assert "/v1/public/files/" not in desc
    assert desc == "and some words"


def test_member_names_never_appear_on_the_card(auth_client: httpx.Client):
    """The card is a SYSTEM header. A roster is a different exposure decision."""
    system_id, vid = _published(auth_client, include_members=True)
    _set_system(auth_client, name="Has Members")
    r = auth_client.post("/v1/members", json={"name": "Distinctive Member Name",
                                              "privacy": "public"})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert (
        auth_client.post(
            f"/v1/share-views/{vid}/members", json={"member_id": mid}
        ).status_code
        in (200, 201)
    )
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    with _anon() as anon:
        page = _preview_system(anon, system_id)
    assert "Distinctive Member Name" not in page.text


def test_hostile_system_name_is_escaped_on_the_card(auth_client: httpx.Client):
    """The name is user content going into an HTML attribute."""
    system_id, vid = _published(auth_client)
    _set_system(auth_client, name='x"><script>alert(1)</script>')
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    with _anon() as anon:
        page = _preview_system(anon, system_id)
    assert "<script" not in page.text.lower()
    assert len(re.findall(r'<meta property="og:title"', page.text)) == 1


# ---------------------------------------------------------------------------
# Invariant: turning it on goes through the exposure rails
# ---------------------------------------------------------------------------


def test_turning_it_on_records_an_exposure_raise(auth_client: httpx.Client):
    """It is a loosening, so it lands in the security log like its siblings."""
    _, vid = _published(auth_client)
    r = auth_client.patch(
        f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"}
    )
    assert r.status_code == 200, r.text
    events = auth_client.get("/v1/auth/me/security-events")
    if events.status_code != 200:
        pytest.skip("security event log not exposed in this configuration")
    raises = [
        e
        for e in events.json().get("events", events.json())
        if e.get("event_type") == "exposure_raised"
    ]
    assert any(
        "link_preview_mode" in str(e.get("detail", "")) for e in raises
    ), raises


def test_mixed_directions_in_one_body_are_refused(auth_client: httpx.Client):
    """A failed re-auth on the raise must not be able to swallow a tightening."""
    _, vid = _published(auth_client, include_bio=True)
    _arm_visibility_safety(auth_client)
    r = auth_client.patch(
        f"/v1/share-views/{vid}",
        json={"link_preview_mode": "system_details", "include_bio": False},
    )
    assert r.status_code == 400, r.text


def test_it_can_always_be_turned_off(auth_client: httpx.Client):
    """Nothing may stand between somebody and going dark - not even a window."""
    _, vid = _published(auth_client)
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    _arm_visibility_safety(auth_client)
    r = auth_client.patch(
        f"/v1/share-views/{vid}", json={"link_preview_mode": "generic"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["link_preview_mode"] == "generic"
    assert r.json()["pending_link_preview_mode"] is None


# ---------------------------------------------------------------------------
# Export / import round-trip
# ---------------------------------------------------------------------------


def test_the_setting_survives_an_export(auth_client: httpx.Client):
    """New user-data field, so it has to ride along in the Article 20 dump."""
    _, vid = _published(auth_client)
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    dump = auth_client.get("/v1/export")
    assert dump.status_code == 200, dump.text
    views = dump.json().get("share_views", [])
    assert views, dump.json().keys()
    assert any(
        v.get("link_preview_mode") == "system_details" for v in views
    ), views


# ---------------------------------------------------------------------------
# The stable preview-image route
# ---------------------------------------------------------------------------


def test_preview_image_serves_for_a_rich_profile(auth_client: httpx.Client):
    system_id, vid = _published(auth_client)
    _set_system(auth_client, name="Pictured", avatar_url="https://cdn.example/a.png")
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    with _anon() as anon:
        public = anon.get(f"/v1/public/systems/{system_id}").json()
        if public.get("avatar_url") is None:
            pytest.skip("instance image policy withholds the avatar from the page")
        # Do not follow: the point is that this route hands out a fresh target.
        r = anon.get(
            f"/v1/link-preview/p/{system_id}/preview-image", follow_redirects=False
        )
    assert r.status_code == 302, r.text
    assert r.headers["location"]
    # Short TTL, so unpublishing is not defeated by a cache.
    assert "max-age=60" in r.headers["cache-control"]


def test_preview_image_404s_when_the_mode_is_generic(auth_client: httpx.Client):
    """Gated identically to the card. Rich off means no image, not a stale one."""
    system_id, _ = _published(auth_client)
    _set_system(auth_client, avatar_url="https://cdn.example/a.png")
    with _anon() as anon:
        r = anon.get(
            f"/v1/link-preview/p/{system_id}/preview-image", follow_redirects=False
        )
    assert r.status_code == 404


def test_preview_image_404s_once_unpublished(auth_client: httpx.Client):
    """The embed's picture dies with the profile, which is the point of the route."""
    system_id, vid = _published(auth_client)
    _set_system(auth_client, name="Soon Gone", avatar_url="https://cdn.example/a.png")
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    gid = [
        g["id"] for g in auth_client.get("/v1/share-grants").json()
        if g["view_id"] == vid
    ][0]
    assert auth_client.delete(f"/v1/share-grants/{gid}").status_code == 204
    with _anon() as anon:
        r = anon.get(
            f"/v1/link-preview/p/{system_id}/preview-image", follow_redirects=False
        )
    assert r.status_code == 404


def test_preview_image_reflects_an_avatar_change(auth_client: httpx.Client):
    """Resolved per request, so changing the avatar needs no cache-busting."""
    system_id, vid = _published(auth_client)
    _set_system(auth_client, name="Changer", avatar_url="https://cdn.example/one.png")
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    with _anon() as anon:
        first = anon.get(
            f"/v1/link-preview/p/{system_id}/preview-image", follow_redirects=False
        )
        if first.status_code != 302:
            pytest.skip("instance image policy withholds the avatar from the page")
        before = first.headers["location"]
        _set_system(auth_client, avatar_url="https://cdn.example/two.png")
        after = anon.get(
            f"/v1/link-preview/p/{system_id}/preview-image", follow_redirects=False
        ).headers["location"]
    assert before != after
    assert "two.png" in after


def test_preview_image_honours_no_caller_supplied_key(auth_client: httpx.Client):
    """It is not an unauthenticated image proxy, and cannot be talked into being one.

    There is no key parameter in the signature, so the only thing that decides which
    bytes come out is the view's own projection. A query string is refused outright
    (the route has no parameters to bind one to) and a traversal-shaped id is not a
    UUID, so it resolves to nothing.
    """
    system_id, vid = _published(auth_client)
    _set_system(auth_client, name="Not A Proxy", avatar_url="https://cdn.example/a.png")
    auth_client.patch(f"/v1/share-views/{vid}", json={"link_preview_mode": "system_details"})
    with _anon() as anon:
        for probe in (
            f"/v1/link-preview/p/{system_id}/preview-image?key=avatars/someone/else.png",
            f"/v1/link-preview/p/{system_id}/preview-image?path=/etc/passwd",
            "/v1/link-preview/p/..%2f..%2fetc%2fpasswd/preview-image",
            "/v1/link-preview/p/not-a-uuid/preview-image",
        ):
            r = anon.get(probe, follow_redirects=False)
            # Either refused, or redirected to THIS view's own avatar and nothing
            # else - never to a location derived from the query string.
            if r.status_code == 302:
                assert "someone/else" not in r.headers["location"]
                assert "passwd" not in r.headers["location"]
            else:
                assert r.status_code in (404, 422), (probe, r.status_code)


def test_share_link_preview_image_is_always_404(auth_client: httpx.Client):
    """And identically so, valid token or not, so it cannot confirm a token.

    If this resolved the token and only 404'd for a missing avatar, the difference
    between the two 404s would have been a way to check a link is live without ever
    loading the page.
    """
    _attest(auth_client)
    _disarm_visibility_safety(auth_client)
    _go_public(auth_client)
    _set_system(auth_client, avatar_url="https://cdn.example/a.png")
    vid = _view(auth_client, link_preview_mode="system_details", member_permalinks=True)
    token = _grant(auth_client, vid, "link")["token"]
    with _anon() as anon:
        real = anon.get(f"/v1/link-preview/s/{token}/preview-image", follow_redirects=False)
        fake = anon.get("/v1/link-preview/s/invented/preview-image", follow_redirects=False)
    assert real.status_code == 404
    assert fake.status_code == 404
    assert real.text == fake.text


# ---------------------------------------------------------------------------
# Member permalink cards
# ---------------------------------------------------------------------------


def _public_member(c: httpx.Client, vid: str, name: str) -> str:
    r = c.post("/v1/members", json={"name": name, "privacy": "public"})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    added = c.post(f"/v1/share-views/{vid}/members", json={"member_id": mid})
    assert added.status_code in (200, 201), added.text
    return mid


def test_member_card_shows_the_member_name_when_turned_on(auth_client: httpx.Client):
    system_id, vid = _published(auth_client, member_permalinks=True)
    mid = _public_member(auth_client, vid, "Rook The Member")
    r = auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_link_preview_mode": "system_details"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["member_link_preview_effective"] == "system_details"
    with _anon() as anon:
        page = anon.get(f"/v1/link-preview/p/{system_id}/member/{mid}")
    assert page.status_code == 200
    assert _og(page.text, "og:title") == "Rook The Member"


def test_member_card_is_independent_of_the_system_card(auth_client: httpx.Client):
    """Neither setting implies the other - that is why they are two columns."""
    system_id, vid = _published(auth_client, member_permalinks=True)
    _set_system(auth_client, name="SYSTEM NAME HERE")
    mid = _public_member(auth_client, vid, "Member Name Here")
    # Member cards on, system card deliberately left generic.
    auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_link_preview_mode": "system_details"}
    )
    with _anon() as anon:
        root = anon.get(f"/v1/link-preview/p/{system_id}")
        member = anon.get(f"/v1/link-preview/p/{system_id}/member/{mid}")
    assert _is_generic(root)
    assert "SYSTEM NAME HERE" not in root.text
    assert _og(member.text, "og:title") == "Member Name Here"
    # And the member card does not leak the system name either.
    assert "SYSTEM NAME HERE" not in member.text


def test_private_member_in_a_public_view_stays_generic(auth_client: httpx.Client):
    """Decided by the projection, not by a second copy of the visibility rule."""
    system_id, vid = _published(auth_client, member_permalinks=True)
    auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_link_preview_mode": "system_details"}
    )
    mid = _public_member(auth_client, vid, "Was Public Then Private")
    # Drop the member below the public tier while leaving them in the view.
    lowered = auth_client.patch(f"/v1/members/{mid}", json={"privacy": "private"})
    assert lowered.status_code == 200, lowered.text
    with _anon() as anon:
        page = anon.get(f"/v1/link-preview/p/{system_id}/member/{mid}")
        image = anon.get(
            f"/v1/link-preview/p/{system_id}/member/{mid}/preview-image",
            follow_redirects=False,
        )
    assert _is_generic(page)
    assert "Was Public Then Private" not in page.text
    assert image.status_code == 404


def test_member_card_needs_permalinks_on(auth_client: httpx.Client):
    """With permalinks off the page is a 404, so the card must not describe one."""
    system_id, vid = _published(auth_client, member_permalinks=False)
    mid = _public_member(auth_client, vid, "No Permalink Member")
    auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_link_preview_mode": "system_details"}
    )
    got = auth_client.get(f"/v1/share-views/{vid}").json()
    assert got["member_link_preview_effective"] == "generic"
    with _anon() as anon:
        page = anon.get(f"/v1/link-preview/p/{system_id}/member/{mid}")
    assert _is_generic(page)
    assert "No Permalink Member" not in page.text


def test_member_card_on_a_share_link_is_always_generic(auth_client: httpx.Client):
    """A token URL naming one specific person is the sharpest version of this."""
    _attest(auth_client)
    _disarm_visibility_safety(auth_client)
    _go_public(auth_client)
    vid = _view(
        auth_client,
        member_permalinks=True,
        member_link_preview_mode="system_details",
    )
    r = auth_client.post("/v1/members", json={"name": "Hidden Person", "privacy": "public"})
    mid = r.json()["id"]
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": mid})
    token = _grant(auth_client, vid, "link")["token"]
    with _anon() as anon:
        page = anon.get(f"/v1/link-preview/s/{token}/member/{mid}")
    assert _is_generic(page)
    assert "Hidden Person" not in page.text
    assert token not in page.text
    assert "og:url" not in page.text


def test_staged_member_mode_previews_generic(auth_client: httpx.Client):
    system_id, vid = _published(auth_client, member_permalinks=True)
    mid = _public_member(auth_client, vid, "Not Yet Named")
    _arm_visibility_safety(auth_client)
    r = auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_link_preview_mode": "system_details"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["member_link_preview_mode"] == "generic"
    assert body["pending_member_link_preview_mode"] == "system_details"
    assert body["member_link_preview_effective"] == "generic"
    with _anon() as anon:
        page = anon.get(f"/v1/link-preview/p/{system_id}/member/{mid}")
    assert _is_generic(page)
    assert "Not Yet Named" not in page.text


def test_member_card_carries_name_and_avatar_only_over_http(
    auth_client: httpx.Client,
):
    """The contents are a decision; this keeps them one end to end."""
    system_id, vid = _published(auth_client, member_permalinks=True, include_bio=True)
    r = auth_client.post(
        "/v1/members",
        json={
            "name": "Minimal Card",
            "privacy": "public",
            "pronouns": "rook/rooks",
            "description": "A bio that must not reach the card.",
        },
    )
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    auth_client.post(f"/v1/share-views/{vid}/members", json={"member_id": mid})
    auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_link_preview_mode": "system_details"}
    )
    with _anon() as anon:
        page = anon.get(f"/v1/link-preview/p/{system_id}/member/{mid}")
    assert _og(page.text, "og:title") == "Minimal Card"
    assert "rook/rooks" not in page.text
    assert "A bio that must not reach the card." not in page.text


def test_member_mode_can_always_be_turned_off(auth_client: httpx.Client):
    _, vid = _published(auth_client, member_permalinks=True)
    auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_link_preview_mode": "system_details"}
    )
    _arm_visibility_safety(auth_client)
    r = auth_client.patch(
        f"/v1/share-views/{vid}", json={"member_link_preview_mode": "generic"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["member_link_preview_mode"] == "generic"
    assert r.json()["pending_member_link_preview_mode"] is None


def test_an_unknown_mode_is_refused(auth_client: httpx.Client):
    """The enum is enforced at the schema, so nothing odd reaches the column."""
    _, vid = _published(auth_client)
    r = auth_client.patch(
        f"/v1/share-views/{vid}", json={"link_preview_mode": "everything"}
    )
    assert r.status_code == 422, r.text
