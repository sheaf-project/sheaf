"""Unit tests for per-view link previews (the generic / system-details card).

Pure logic, no DB and no docker stack. What is decidable on the host: the
markdown-to-snippet flattener and its cap, the card builder's fail-to-generic
behaviour, the rendered document's escaping, the relative-to-absolute media URL
fix, and the wiring of the two preview modes into the staged-exposure machinery
that gives it re-auth and a grace window.

The end-to-end behaviour - which URL gets which card once a grant, a grace window
and a suppression are involved - lives in test_link_preview_api.py, which needs
the stack.

The tests are grouped by the invariant they defend, and the ones that matter most
are the three in "invariants" at the bottom: a secret URL cannot get a rich card,
staged publishing previews generic, and the card cannot carry a field the page
does not already serve.
"""

from __future__ import annotations

import re

import pytest

from sheaf.models.share import LinkPreviewMode, ShareView
from sheaf.schemas.public_profile import PublicSystemView
from sheaf.schemas.share import ShareViewCreate, ShareViewRead, ShareViewUpdate
from sheaf.services.link_preview import (
    DESCRIPTION_SNIPPET_CHARS,
    GENERIC_DESCRIPTION,
    GENERIC_TITLE,
    MEMBER_CARD_DESCRIPTION,
    absolute_url,
    build_link_preview,
    build_member_preview,
    generic_image_url,
    generic_preview,
    plain_text_snippet,
    render_preview_html,
)
from sheaf.services.sharing import (
    EXPOSURE_FLAGS,
    flag_direction,
    link_preview_effective,
    member_link_preview_effective,
    promote_view_flags,
)


def _projection(**over) -> PublicSystemView:
    """A system projection with the shape the anonymous route really returns."""
    base = dict(
        id="11111111-1111-1111-1111-111111111111",
        name="The Example System",
        description=None,
        avatar_url=None,
        color=None,
        tag=None,
        member_count=3,
        member_permalinks=False,
    )
    base.update(over)
    return PublicSystemView(**base)


# --- Snippet flattening -----------------------------------------------------


def test_snippet_flattens_common_markdown():
    got = plain_text_snippet("# Hello\n\nWe are a **system** of ~six~ people.")
    assert got == "Hello We are a system of six people."


def test_snippet_returns_none_for_nothing():
    for empty in (None, "", "   \n\t ", "```\n```"):
        assert plain_text_snippet(empty) is None


def test_snippet_drops_image_markup_entirely():
    """An image is not text, and its target is the thing that must not survive."""
    got = plain_text_snippet("![a picture](/some/path.png) hello")
    assert got == "hello"


def test_snippet_keeps_link_text_and_drops_link_target():
    got = plain_text_snippet("Read [our carrd](https://example.com/secret) please")
    assert got == "Read our carrd please"


# The projection substitutes a SIGNED media capability into an owned image ref
# before anything here sees the description. A capability in an og:description
# would be copied into a chat service's cache, so every construct that can carry
# a URL is checked, not just the obvious one.
_SIGNED = "/v1/public/files/bios/abc/def.png?token=deadbeefcafe&expires=1700000000"


@pytest.mark.parametrize(
    "description",
    [
        f"![bio image]({_SIGNED})",
        f"![bio image]({_SIGNED}) and some words",
        f"words [a link]({_SIGNED}) words",
        f"words <{_SIGNED}> words",
        f"[ref]: {_SIGNED}\nSee [the thing][ref]",
        f'<img src="{_SIGNED}"> words',
        f"nested ![outer ![inner]({_SIGNED})]({_SIGNED})",
    ],
    ids=[
        "image-only",
        "image-plus-text",
        "inline-link",
        "autolink",
        "reference-definition",
        "raw-html-img",
        "nested-images",
    ],
)
def test_snippet_never_leaks_a_signed_capability(description: str):
    got = plain_text_snippet(description) or ""
    assert "token=" not in got
    assert "expires=" not in got
    assert "/v1/public/files/" not in got
    assert "deadbeefcafe" not in got


def test_snippet_leaves_prose_urls_alone():
    """A URL the owner typed as text is page content, not a leaked capability."""
    got = plain_text_snippet("find us at https://example.com/us")
    assert got == "find us at https://example.com/us"


def test_snippet_caps_length_and_marks_truncation():
    got = plain_text_snippet("word " * 200)
    assert got is not None
    # The ellipsis is one character on top of the cap, never more.
    assert len(got) <= DESCRIPTION_SNIPPET_CHARS + 1
    assert got.endswith("…")


def test_snippet_truncates_on_a_word_boundary_when_there_is_one():
    got = plain_text_snippet("alpha bravo " * 40)
    assert got is not None
    assert "…" in got
    # No half-word before the ellipsis.
    assert got.removesuffix("…").rstrip().split()[-1] in {"alpha", "bravo"}


def test_snippet_does_not_collapse_to_nothing_on_one_long_token():
    """A single unbroken token must still yield a snippet, not an empty string."""
    got = plain_text_snippet("x" * 400)
    assert got is not None
    assert len(got.removesuffix("…")) == DESCRIPTION_SNIPPET_CHARS


def test_snippet_never_contains_a_newline():
    got = plain_text_snippet("line one\n\nline two\r\nline three")
    assert got == "line one line two line three"


# --- Card building ----------------------------------------------------------


def test_generic_card_says_nothing_about_the_system():
    card = generic_preview("https://example.test/p/abc")
    assert card.rich is False
    assert card.title == GENERIC_TITLE
    assert card.description == GENERIC_DESCRIPTION
    assert card.image_url is None


def test_generic_card_can_carry_the_instance_logo():
    """The logo is the instance's, not the subject's.

    It stops a generic link unfurling as a bare line of text, and it reveals
    nothing: the same bytes for every URL on the instance, so it cannot
    distinguish a profile that exists from one that does not. That is why it is
    a static asset rather than the per-subject image route.
    """
    logo = "https://example.test/og-image.png"
    card = generic_preview("https://example.test/p/abc", image_url=logo)
    assert card.image_url == logo
    # Still generic in every other respect: a picture is not a disclosure.
    assert card.rich is False
    assert card.title == GENERIC_TITLE
    assert card.description == GENERIC_DESCRIPTION

    # And a made-up URL gets the identical card, logo included.
    invented = generic_preview("https://example.test/p/nope", image_url=logo)
    assert invented.image_url == card.image_url
    assert invented.title == card.title
    assert invented.description == card.description


def test_generic_image_url_needs_an_origin():
    """No configured base URL means no absolute URL to build, so no image,
    rather than a relative one every crawler would drop."""
    assert generic_image_url(None) is None
    assert generic_image_url("") is None
    assert generic_image_url("https://example.test") == (
        "https://example.test/og-image.png"
    )
    # A trailing slash must not produce a doubled one.
    assert generic_image_url("https://example.test/") == (
        "https://example.test/og-image.png"
    )


def test_rich_card_uses_the_projection_fields():
    card = build_link_preview(
        _projection(description="We are **six**.", avatar_url="/v1/public/files/a/b.png"),
        rich=True,
        page_url="https://example.test/p/abc",
        image_url="https://example.test/p/abc/preview-image",
    )
    assert card.rich is True
    assert card.title == "The Example System"
    assert card.description == "We are six."
    # The card points at the STABLE per-request image route, never at the
    # projection's short-lived signed avatar URL.
    assert card.image_url == "https://example.test/p/abc/preview-image"


def test_rich_false_gives_the_generic_card_even_with_a_projection():
    """The flag is the caller's decision and the builder must obey it."""
    card = build_link_preview(
        _projection(description="We are six."),
        rich=False,
        page_url="https://example.test/p/abc",
    )
    assert card.rich is False
    assert card.title == GENERIC_TITLE
    assert "six" not in card.description


def test_no_projection_gives_the_generic_card():
    card = build_link_preview(
        None, rich=True, page_url="https://example.test/p/abc"
    )
    assert card.rich is False


def test_rich_card_with_no_description_falls_back_to_the_generic_blurb():
    card = build_link_preview(
        _projection(description=None),
        rich=True,
        page_url="https://example.test/p/abc",
    )
    assert card.rich is True
    assert card.title == "The Example System"
    assert card.description == GENERIC_DESCRIPTION


def test_rich_card_with_an_image_only_description_falls_back_too():
    """The snippet flattens to nothing, so the card must not carry an empty one."""
    card = build_link_preview(
        _projection(description=f"![pic]({_SIGNED})"),
        rich=True,
        page_url="https://example.test/p/abc",
    )
    assert card.description == GENERIC_DESCRIPTION


def test_blank_name_falls_back_to_generic():
    card = build_link_preview(
        _projection(name="   "),
        rich=True,
        page_url="https://example.test/p/abc",
    )
    assert card.rich is False


# --- Absolute media URLs ----------------------------------------------------


def test_relative_media_url_is_made_absolute():
    """Crawlers drop a root-relative og:image, which is what the SPA shell has."""
    assert (
        absolute_url("/v1/public/files/a/b.png", "https://example.test/")
        == "https://example.test/v1/public/files/a/b.png"
    )


def test_absolute_media_url_is_passed_through():
    assert absolute_url("https://cdn.example/a.png", "https://x.test") == (
        "https://cdn.example/a.png"
    )


@pytest.mark.parametrize(
    "url,origin",
    [(None, "https://x.test"), ("", "https://x.test"), ("/a.png", ""), ("a.png", "https://x.test")],
)
def test_unusable_media_url_is_dropped_rather_than_guessed(url, origin):
    assert absolute_url(url, origin) is None


# --- Rendering --------------------------------------------------------------


def test_rendered_document_carries_the_expected_meta_tags():
    html = render_preview_html(
        build_link_preview(
            _projection(description="Hello.", avatar_url="/v1/public/files/a/b.png"),
            rich=True,
            page_url="https://example.test/p/abc",
            image_url="https://example.test/p/abc/preview-image",
        )
    )
    for needle in (
        '<meta property="og:image" content="https://example.test/p/abc/preview-image" />',
        '<meta property="og:title" content="The Example System" />',
        '<meta property="og:description" content="Hello." />',
        '<meta property="og:url" content="https://example.test/p/abc" />',
        '<meta name="twitter:title" content="The Example System" />',
        '<meta name="robots" content="noindex, nofollow" />',
    ):
        assert needle in html


def test_rendered_document_omits_og_url_when_there_is_none():
    """A token URL must not be copied into the card's own metadata."""
    html = render_preview_html(generic_preview(None))
    assert "og:url" not in html


def test_rendered_document_has_no_script_or_style():
    html = render_preview_html(generic_preview("https://example.test/p/a"))
    assert "<script" not in html.lower()
    assert "<style" not in html.lower()


@pytest.mark.parametrize(
    "hostile",
    [
        '"><script>alert(1)</script>',
        "\" /><meta property='og:title' content='pwned'",
        "Ampersand & angle < brackets >",
        "'single' and \"double\" quotes",
    ],
)
def test_hostile_system_name_cannot_break_out_of_the_attribute(hostile: str):
    """The name is user content going straight into an HTML attribute."""
    html = render_preview_html(
        build_link_preview(
            _projection(name=hostile),
            rich=True,
            page_url="https://example.test/p/abc",
        )
    )
    assert "<script" not in html.lower()
    # Exactly one og:title tag: an injected second one would mean the first
    # attribute was escaped out of.
    assert len(re.findall(r'<meta property="og:title"', html)) == 1
    for raw in ("<", ">"):
        # No unescaped angle bracket survives inside any content attribute.
        assert raw not in re.search(
            r'<meta property="og:title" content="([^"]*)"', html
        ).group(1)


def test_hostile_description_cannot_break_out_either():
    html = render_preview_html(
        build_link_preview(
            _projection(description='"><script>alert(1)</script> hi'),
            rich=True,
            page_url="https://example.test/p/abc",
        )
    )
    assert "<script" not in html.lower()
    assert len(re.findall(r'<meta property="og:description"', html)) == 1


# --- Wiring into the exposure rail ------------------------------------------


def test_flag_is_an_exposure_flag():
    """Turning a rich card on is a loosening, so it takes the loosening door.

    Membership in this tuple is what gives it the publishing-availability
    refusal, the step-up re-auth, the grace-window staging, the mixed-direction
    refusal and the EXPOSURE_RAISED audit trail - all of it, without the update
    endpoint naming the flag.
    """
    assert "link_preview_mode" in EXPOSURE_FLAGS


def test_flag_has_a_pending_twin_on_the_model():
    assert hasattr(ShareView, "pending_link_preview_mode")


def test_pending_flip_is_promoted_by_the_sweep():
    view = ShareView(
        link_preview_mode=LinkPreviewMode.GENERIC.value,
        pending_link_preview_mode=LinkPreviewMode.SYSTEM_DETAILS.value,
    )
    promote_view_flags(view)
    # `promote_view_flags` copies whatever is parked without inspecting it, which
    # is why a string-valued flag needed no change to the sweep.
    assert view.link_preview_mode == LinkPreviewMode.SYSTEM_DETAILS.value
    assert view.pending_link_preview_mode is None


def test_schemas_default_to_the_quiet_option():
    """A view created without an opinion previews generic."""
    assert ShareViewCreate(name="v").link_preview_mode == LinkPreviewMode.GENERIC
    assert (
        ShareViewCreate(name="v").member_link_preview_mode == LinkPreviewMode.GENERIC
    )
    # An update that does not mention them leaves them alone.
    assert ShareViewUpdate().link_preview_mode is None
    assert ShareViewUpdate().member_link_preview_mode is None


def test_view_read_reports_the_effective_mode():
    for field in ("link_preview_effective", "member_link_preview_effective"):
        assert field in ShareViewRead.model_fields
        assert ShareViewRead.model_fields[field].default == LinkPreviewMode.GENERIC


# --- The invariants ---------------------------------------------------------


def test_effective_mode_is_generic_without_a_serving_public_grant():
    """A view reachable only by share LINK never previews rich.

    The link's URL is itself the secret, so a rich card would hand the system's
    name to whatever cache the link was pasted into - exactly the exposure that
    keeping the URL quiet was avoiding. The owner is told "generic" rather than
    left to infer it from a switch that is on.
    """
    view = ShareView(link_preview_mode=LinkPreviewMode.SYSTEM_DETAILS.value)
    assert link_preview_effective(view, serves_public_grant=False) == "generic"
    assert link_preview_effective(view, serves_public_grant=True) == "system_details"


def test_effective_mode_is_generic_while_the_flip_is_still_staged():
    """A grace window that held the profile back but leaked the name would be no
    grace window at all. Only the LIVE flag counts."""
    view = ShareView(
        link_preview_mode=LinkPreviewMode.GENERIC.value,
        pending_link_preview_mode=LinkPreviewMode.SYSTEM_DETAILS.value,
    )
    assert link_preview_effective(view, serves_public_grant=True) == "generic"


def test_rich_card_fields_are_a_subset_of_the_public_projection():
    """The card can only carry what the anonymous JSON route already serves.

    Enforced structurally: every string on a rich card has to be traceable to a
    field of `PublicSystemView`, which is the fail-closed contract for what an
    unauthenticated visitor gets. If somebody later adds an encrypted or
    owner-only value to a card, this is the test that should stop being true.
    """
    public_fields = set(PublicSystemView.model_fields)
    assert {"name", "description", "avatar_url"} <= public_fields

    card = build_link_preview(
        _projection(description="Bio text.", avatar_url="/v1/public/files/a/b.png"),
        rich=True,
        page_url="https://example.test/p/abc",
        image_url="https://example.test/p/abc/preview-image",
    )
    # Title is the projected name; description derives from the projected
    # description; the image is this URL's own stable image route, which resolves
    # the projected avatar per request. Nothing else.
    assert card.title == "The Example System"
    assert card.description == "Bio text."
    assert card.image_url == "https://example.test/p/abc/preview-image"


def test_rich_card_does_not_carry_the_member_count():
    """Published, but deliberately left off the card.

    `member_count` is in the public payload, so putting it on a card would not
    break the "nothing the page does not serve" rule - it is omitted because a
    roster size is an aggregate that reads differently in a chat embed than on a
    page somebody chose to open, and nobody asked for it. Pinned so it is a
    decision rather than something that drifts in later.
    """
    card = build_link_preview(
        _projection(member_count=23, description="Bio."),
        rich=True,
        page_url="https://example.test/p/abc",
    )
    rendered = render_preview_html(card)
    assert "23" not in rendered


# --- The member permalink card ----------------------------------------------


def test_member_card_carries_name_and_avatar_only():
    """Pinned so the contents stay a decision rather than drifting in.

    `PublicMemberView` also has pronouns, a bio, colour and custom field values,
    and a member permalink page shows them. A card is not the page: it names one
    specific person to everyone in a channel, without any of them opening
    anything, so it carries the two fields that make a card recognisable and
    stops. Each of the others is a separate decision nobody has made.
    """
    card = build_member_preview(
        name="Rook",
        image_url="https://example.test/p/s/member/m/preview-image",
        page_url="https://example.test/p/s/member/m",
    )
    assert card.rich is True
    assert card.title == "Rook"
    assert card.image_url == "https://example.test/p/s/member/m/preview-image"
    # The description is a fixed blurb, NOT the member's bio, even where a view
    # publishes bios: a bio is written for people who came to look at the page.
    assert card.description == MEMBER_CARD_DESCRIPTION

    rendered = render_preview_html(card)
    for leaked in ("they/them", "pronoun", "Favourite colour", "bio", "fronting"):
        assert leaked.lower() not in rendered.lower()


def test_member_card_falls_back_to_generic_without_a_name():
    for blank in (None, "", "   "):
        card = build_member_preview(
            name=blank, image_url=None, page_url="https://example.test/p/s/member/m"
        )
        assert card.rich is False
        assert card.title == GENERIC_TITLE


def test_member_card_escapes_a_hostile_name():
    html = render_preview_html(
        build_member_preview(
            name='Rook"><script>alert(1)</script>',
            image_url=None,
            page_url="https://example.test/p/s/member/m",
        )
    )
    assert "<script" not in html.lower()
    assert len(re.findall(r'<meta property="og:title"', html)) == 1


# --- The two modes are independent ------------------------------------------


def test_member_mode_is_its_own_exposure_flag():
    """A separate column, not a third rung on the system dial.

    A system card reveals the system's name, avatar and description snippet; a
    member card reveals one member's name and avatar. Neither contains the other,
    so an ordered dial would have forced system details on as the price of member
    cards - a constraint invented by the column rather than by privacy.
    """
    assert "member_link_preview_mode" in EXPOSURE_FLAGS
    assert hasattr(ShareView, "pending_member_link_preview_mode")


def test_the_two_modes_do_not_imply_each_other():
    system_only = ShareView(
        link_preview_mode=LinkPreviewMode.SYSTEM_DETAILS.value,
        member_link_preview_mode=LinkPreviewMode.GENERIC.value,
        member_permalinks=True,
    )
    assert link_preview_effective(system_only, serves_public_grant=True) == (
        LinkPreviewMode.SYSTEM_DETAILS.value
    )
    assert member_link_preview_effective(system_only, serves_public_grant=True) == (
        LinkPreviewMode.GENERIC.value
    )

    member_only = ShareView(
        link_preview_mode=LinkPreviewMode.GENERIC.value,
        member_link_preview_mode=LinkPreviewMode.SYSTEM_DETAILS.value,
        member_permalinks=True,
    )
    assert link_preview_effective(member_only, serves_public_grant=True) == (
        LinkPreviewMode.GENERIC.value
    )
    assert member_link_preview_effective(member_only, serves_public_grant=True) == (
        LinkPreviewMode.SYSTEM_DETAILS.value
    )


def test_member_mode_needs_permalinks_on():
    """With permalinks off the URL is a 404, so there is nothing to put a card on."""
    view = ShareView(
        member_link_preview_mode=LinkPreviewMode.SYSTEM_DETAILS.value,
        member_permalinks=False,
    )
    assert member_link_preview_effective(view, serves_public_grant=True) == (
        LinkPreviewMode.GENERIC.value
    )


def test_member_mode_is_generic_without_a_serving_public_grant():
    """Same rule as its sibling: a share LINK never gets a rich card."""
    view = ShareView(
        member_link_preview_mode=LinkPreviewMode.SYSTEM_DETAILS.value,
        member_permalinks=True,
    )
    assert member_link_preview_effective(view, serves_public_grant=False) == (
        LinkPreviewMode.GENERIC.value
    )


def test_member_mode_is_generic_while_staged():
    view = ShareView(
        member_link_preview_mode=LinkPreviewMode.GENERIC.value,
        pending_member_link_preview_mode=LinkPreviewMode.SYSTEM_DETAILS.value,
        member_permalinks=True,
    )
    assert member_link_preview_effective(view, serves_public_grant=True) == (
        LinkPreviewMode.GENERIC.value
    )


# --- The generalised raise/lower test ---------------------------------------


def test_flag_direction_handles_booleans():
    view = ShareView(include_bio=False, include_members=True)
    assert flag_direction(view, "include_bio", True) == 1
    assert flag_direction(view, "include_bio", False) == 0
    assert flag_direction(view, "include_members", False) == -1
    assert flag_direction(view, "include_members", True) == 0
    # Field not sent at all.
    assert flag_direction(view, "include_bio", None) == 0


def test_flag_direction_handles_the_mode_enum():
    """The reason the enum needed no staging path of its own.

    `value is True` is meaningless for a string, so the raise/lower question is
    asked of an ordering instead, and booleans just have the obvious two-value one.
    """
    generic = ShareView(link_preview_mode=LinkPreviewMode.GENERIC.value)
    assert flag_direction(generic, "link_preview_mode", "system_details") == 1
    assert flag_direction(generic, "link_preview_mode", "generic") == 0

    rich = ShareView(link_preview_mode=LinkPreviewMode.SYSTEM_DETAILS.value)
    assert flag_direction(rich, "link_preview_mode", "generic") == -1
    assert flag_direction(rich, "link_preview_mode", "system_details") == 0


def test_flag_direction_treats_an_unknown_value_as_no_change():
    """Cannot happen past the schema, and must not read as a raise if it did."""
    view = ShareView(link_preview_mode=LinkPreviewMode.GENERIC.value)
    assert flag_direction(view, "link_preview_mode", "nonsense") == 0
    assert flag_direction(view, "link_preview_mode", True) == 0


def test_every_exposure_flag_has_a_pending_twin():
    """Including the two string-valued ones - the sweep relies on it."""
    for flag in EXPOSURE_FLAGS:
        assert flag in ShareView.__table__.c
        assert f"pending_{flag}" in ShareView.__table__.c
