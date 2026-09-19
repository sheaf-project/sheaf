"""Crawler-facing link previews for the public share surface.

When somebody pastes a profile URL into a chat, the unfurler on the other end
fetches that URL and reads its `<meta>` tags. It does not run JavaScript, so the
single-page app cannot answer it: whatever is in the static shell
(`web/index.html`) is what every Sheaf URL has ever unfurled as, which is one
generic card for the whole instance. This module builds the per-URL answer, as a
small standalone HTML document with nothing in it but metadata.

Two modes, and which one a URL gets is a safety decision rather than a taste
one:

- **generic** - says only that the link is a Sheaf profile. Byte-identical for a
  link that resolves, a link that was revoked, a link inside its grace window,
  and a link that never existed, so the card is never an oracle.
- **system details** - the system's name, avatar and a short snippet of its
  description, so a shared link looks like a profile instead of an anonymous
  blob.

Four rules hold this to the one property that matters, which is that a preview
must never reveal more than an unauthenticated visitor already sees by opening
the URL:

1. **The rich card is built from `share_projection.project_system`** - the exact
   call the anonymous JSON route makes, with the same view, through the same
   resolver. Every field it can put on a card is therefore a field that URL
   already serves to anyone who asks, and if the projection ever narrows, the
   card narrows with it. There is no second path to the data and nothing here
   reads a `System` column directly.
2. **Only a `public` grant can ever produce a rich card.** A share-link URL is
   the secret - that is the entire point of the opaque token - so pasting one
   into a chat with the system's name attached would hand the third party's cache
   exactly what keeping the URL quiet was protecting. `/s/` never consults the
   view's setting; see `build_link_preview`, which does not take a view at all.
3. **Anything staged is not yet live, so it previews as generic.** This is not a
   check here; it falls out of resolving through `resolve_public_grant`, which
   already refuses a pending grant, a system whose raise to public is still
   parked in `pending_privacy`, and a suppressed account. The view's own staged
   raise lives in `pending_link_preview_mode` and leaves the live column alone.
4. **The description snippet is untrusted markdown heading into an HTML
   attribute.** `plain_text_snippet` strips it to text - critically including
   every image and link TARGET, because by the time the projection has run, an
   owned image ref has become a signed media capability, and a capability has no
   business being copied into a chat service's cache. Then it is capped and
   `html.escape`d at render.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from sheaf.schemas.public_profile import PublicSystemView

# How much of a system's description may reach a third party's cache.
#
# 200 characters, chosen from the low end of what unfurlers display rather than
# the high end of what they accept: Discord shows around 300 and Twitter around
# 200, so at 200 nothing truncates the snippet a second time in a place we
# cannot see, and the owner gets the same card everywhere. It is also a cap on
# exposure, not just on layout - the snippet is the one piece of free text that
# leaves the instance without anybody clicking anything, so the smallest useful
# amount is the right amount.
DESCRIPTION_SNIPPET_CHARS = 200

# The card every URL gets unless a rich one has been deliberately turned on and
# is actually live. Deliberately identical for `/p/` and `/s/`, and identical for
# a URL that resolves and one that does not: the moment the generic card varies,
# it starts answering questions about whether a link is real.
GENERIC_TITLE = "A shared system profile"
GENERIC_DESCRIPTION = (
    "A public system profile powered by Sheaf, the open-source plural system "
    "tracker. Open the link to see it."
)

# A member permalink's card. The member's NAME is the title; this is the
# description, and it is deliberately not their bio. A card carries name and
# avatar only - see `build_member_preview`.
MEMBER_CARD_DESCRIPTION = (
    "A member of a public system profile powered by Sheaf. Open the link to see it."
)

SITE_NAME = "Sheaf"


@dataclass(frozen=True)
class LinkPreview:
    """Everything the rendered document needs, and nothing that is not metadata.

    `rich` is carried so tests and the owner-facing report can assert on which
    card was produced without re-deriving it from the strings.
    """

    title: str
    description: str
    # Absolute URL, or None. Crawlers overwhelmingly require an absolute
    # `og:image`; a root-relative one (which is what the static shell has always
    # carried) is silently dropped by most of them.
    image_url: str | None
    # Absolute canonical URL, or None. None for a token URL: `og:url` would copy
    # the bearer token into the card itself, and some unfurlers display it.
    page_url: str | None
    rich: bool


# --- markdown -> plain text -------------------------------------------------
#
# Ordered, and the order is load-bearing. Images go first so that `![a](sig)`
# cannot survive as `[a](sig)` and then be read by the link rule; fenced code
# fences go before inline code so a fence is not mistaken for two empty spans.

# `![alt](target)` and `![alt][ref]` - dropped whole, alt text included. Alt
# text is owner-written and safe, but keeping it would mean a card whose text
# came from a place the page renders as a picture, which reads as a bug the one
# time it matters. The target is the point: after the projection ran, an owned
# image ref here is a signed media capability.
_IMAGE = re.compile(r"!\[[^\]]*\]\s*(?:\([^)]*\)|\[[^\]]*\])")
# `[text](target)` and `[text][ref]` - keep the text, drop the target.
_LINK = re.compile(r"\[([^\]]*)\]\s*(?:\([^)]*\)|\[[^\]]*\])")
# `<https://...>` autolinks, and any HTML tag - INCLUDING one with attributes,
# which is why this is not the tighter `<[^>\s]+>` it started as. A raw
# `<img src="/v1/public/files/...?token=...">` has spaces in it, so a no-
# whitespace pattern walked straight past the tag and left the capability in the
# text. Eating a stray `a < b > c` from prose is the price, and it is the right
# way round to be wrong.
_TAG = re.compile(r"<[^<>]*>")
# Fenced code delimiters (``` / ~~~) and any leading info string.
_FENCE = re.compile(r"^\s*(?:`{3,}|~{3,}).*$", re.MULTILINE)
# Block prefixes at the start of a line: ATX headings, blockquotes, list
# bullets, ordered-list markers, and setext underlines.
_BLOCK_PREFIX = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+|\d+[.)][ \t]+)", re.MULTILINE
)
_SETEXT = re.compile(r"^[ \t]*(?:=+|-{2,})[ \t]*$", re.MULTILINE)
# A table's delimiter row (`|---|:--:|`) is punctuation, not content, so it goes
# whole rather than surviving as a run of dashes in the middle of a sentence.
_TABLE_DELIM = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$",
    re.MULTILINE,
)
# Remaining table pipes and cell padding collapse to spaces rather than
# vanishing, so two cells do not run into one word.
_TABLE_PIPE = re.compile(r"\|")
# Inline emphasis / strikethrough / code markers. Stripped as characters rather
# than matched as pairs: an unbalanced marker is common in real prose and a
# pair-matching rule leaves it behind, which looks worse than removing it.
_INLINE_MARKS = re.compile(r"[*_~`]+")
# Footnote / reference definitions on their own line.
_REF_DEF = re.compile(r"^[ \t]*\[[^\]]*\]:[ \t]*\S+.*$", re.MULTILINE)
_WHITESPACE = re.compile(r"\s+")

# Belt and braces, and explicitly NOT the mechanism this relies on: the markdown
# passes above are what remove URL targets, and the loop below is what makes them
# hold under nesting. This last pass exists because the consequence of one missed
# construct is a signed media capability in somebody else's cache, and a
# whitespace-delimited token containing `token=` or this instance's own media path
# is never prose worth preserving. It can only affect text that literally contains
# one of those, so it costs nothing on real descriptions.
#
# The path fragment mirrors `files.sign_public_file_url`. If that route ever moves
# this should follow, but the markdown passes would still be doing the real work.
_CAPABILITY_RESIDUE = re.compile(r"\S*(?:token=|/v1/public/files/)\S*")

# How many times the image/link passes are re-run to reach a fixpoint. Nested
# markdown (`![outer ![inner](sig)](sig)`) defeats a single pass, because the
# inner `]` terminates the outer pattern's character class and leaves the outer
# target stranded as plain text. Re-running until nothing changes handles
# arbitrary nesting; the bound stops a pathological input from spinning, and the
# residue pass above catches anything still standing after it.
_NESTING_PASSES = 5


def plain_text_snippet(
    text: str | None, *, cap: int = DESCRIPTION_SNIPPET_CHARS
) -> str | None:
    """Flatten markdown to a single capped line of plain text, or None.

    Returns None for anything that flattens to nothing, so a description made
    entirely of an image or a code fence yields no snippet rather than an empty
    one - the caller then falls back to the generic description instead of
    publishing a blank card.

    The guarantee that matters is that no signed media capability survives. By the
    time a description reaches here the projection has already rewritten every
    owned image ref into a short-lived signed URL, and an `og:description` is
    copied verbatim into a chat service's cache - so every construct that can
    carry a URL (image, link, autolink, reference definition, raw HTML tag) has
    its target removed, the image and link passes run to a fixpoint so nesting
    cannot smuggle one through, and `_CAPABILITY_RESIDUE` sweeps up behind them.

    Bare URLs the owner typed as prose are left alone: they are page text like any
    other, and mangling them would be a different kind of wrong.

    Truncation cuts on a word boundary where there is one in reach and appends a
    single ellipsis, so the cap is a cap on what leaves the instance rather than
    a mid-word guess at what the owner meant.
    """
    if not text:
        return None
    out = text
    out = _REF_DEF.sub(" ", out)
    out = _FENCE.sub(" ", out)
    # To a fixpoint, not once: see `_NESTING_PASSES`.
    for _ in range(_NESTING_PASSES):
        before = out
        out = _IMAGE.sub(" ", out)
        out = _LINK.sub(r"\1", out)
        if out == before:
            break
    out = _TAG.sub(" ", out)
    out = _CAPABILITY_RESIDUE.sub(" ", out)
    out = _TABLE_DELIM.sub(" ", out)
    out = _SETEXT.sub(" ", out)
    out = _BLOCK_PREFIX.sub("", out)
    out = _TABLE_PIPE.sub(" ", out)
    out = _INLINE_MARKS.sub("", out)
    out = _WHITESPACE.sub(" ", out).strip()
    if not out:
        return None
    if len(out) <= cap:
        return out
    clipped = out[:cap]
    # Only honour a word boundary that is not miles back; otherwise a single
    # long token would shrink the snippet to nothing.
    space = clipped.rfind(" ")
    if space >= cap // 2:
        clipped = clipped[:space]
    return clipped.rstrip() + "…"


# --- building ---------------------------------------------------------------


def generic_preview(page_url: str | None) -> LinkPreview:
    """The card that says only "this is a Sheaf profile"."""
    return LinkPreview(
        title=GENERIC_TITLE,
        description=GENERIC_DESCRIPTION,
        image_url=None,
        page_url=page_url,
        rich=False,
    )


def build_link_preview(
    projection: PublicSystemView | None,
    *,
    rich: bool,
    page_url: str | None,
    image_url: str | None = None,
) -> LinkPreview:
    """Turn a resolved projection into a card, or fall back to the generic one.

    `projection` is the output of `share_projection.project_system` and is the
    ONLY source of system data here - that is what makes "the card cannot reveal
    more than the page" a property of the code rather than a promise. `rich` is
    the caller's already-made decision (view flag on, live, and a `public`
    grant); this function does not re-derive it, so there is one place that
    decides and one place that renders.

    A `None` projection, or `rich=False`, gives the generic card. So does a
    projection with an empty name, which cannot happen through the API but would
    otherwise render a card with a blank title if it ever did.

    `image_url` is passed in rather than derived from `projection.avatar_url`,
    because the card must point at a STABLE address that resolves the avatar per
    request (see `api/link_preview.system_preview_image`), not at the short-lived
    signed URL the projection hands out. Deriving it here is what the first cut did,
    and it meant a card whose picture died with the capability behind it.
    """
    if projection is None or not rich or not projection.name.strip():
        return generic_preview(page_url)
    return LinkPreview(
        title=projection.name.strip(),
        # Falling back to the generic blurb rather than to an empty string: a
        # system with no description still gets a card that explains what the
        # link is.
        description=plain_text_snippet(projection.description) or GENERIC_DESCRIPTION,
        image_url=image_url,
        page_url=page_url,
        rich=True,
    )


def build_member_preview(
    *, name: str | None, image_url: str | None, page_url: str | None
) -> LinkPreview:
    """A member permalink's card: their name and their avatar, and nothing else.

    The narrowness is the design, not an omission to be filled in later. A member
    card names one specific person, which is the sharpest thing this feature can
    put in a chat, so it carries the two fields that make a card recognisable and
    stops. No pronouns, no bio, no custom field values, no group names, no front
    status - several of those are in `PublicMemberView` and would have been
    available, and each one is a separate decision nobody has made. A test pins the
    contents so they stay a decision rather than drifting in.

    In particular the description is a fixed blurb rather than the member's bio,
    even when the view has `include_bio` on: a bio is a paragraph somebody wrote for
    people who came to look at their page, not for everyone in a channel where the
    link was pasted.

    An empty name falls back to the generic card, which cannot happen through the
    projection but would otherwise render a card with a blank title.
    """
    clean = (name or "").strip()
    if not clean:
        return generic_preview(page_url)
    return LinkPreview(
        title=clean,
        description=MEMBER_CARD_DESCRIPTION,
        image_url=image_url,
        page_url=page_url,
        rich=True,
    )


def absolute_url(url: str | None, origin: str) -> str | None:
    """Make a projection URL absolute against `origin`, or drop it.

    The projection hands out same-origin relative media URLs on purpose (see
    `files.sign_public_file_url`) so the public page's strict `img-src 'self'`
    policy holds in every storage mode. A crawler needs the opposite: almost all
    of them ignore a relative `og:image`. Anything already absolute is passed
    through; anything else is dropped rather than guessed at.
    """
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    if not url.startswith("/") or not origin:
        return None
    return origin.rstrip("/") + url


# --- rendering --------------------------------------------------------------


def _meta(kind: str, key: str, value: str | None) -> str:
    if value is None:
        return ""
    safe_key = html.escape(key, quote=True)
    safe_value = html.escape(value, quote=True)
    return f'    <meta {kind}="{safe_key}" content="{safe_value}" />\n'


def render_preview_html(preview: LinkPreview) -> str:
    """The whole document. Metadata plus a sentence, no scripts, no styles.

    Everything interpolated goes through `html.escape` with `quote=True`, which
    is what makes a description containing a quote character a non-event rather
    than an attribute-injection.

    `robots: noindex, nofollow` is on the document as well as the response
    header, matching what the SPA injects on these routes and what the proxy
    sets: unfurling a link somebody chose to send is the point, turning up in a
    search index never was.
    """
    title = html.escape(preview.title)
    parts = [
        "<!doctype html>\n",
        '<html lang="en">\n',
        "  <head>\n",
        '    <meta charset="utf-8" />\n',
        f"    <title>{title}</title>\n",
        '    <meta name="robots" content="noindex, nofollow" />\n',
        '    <meta name="referrer" content="no-referrer" />\n',
        _meta("name", "description", preview.description),
        _meta("property", "og:type", "profile" if preview.rich else "website"),
        _meta("property", "og:site_name", SITE_NAME),
        _meta("property", "og:title", preview.title),
        _meta("property", "og:description", preview.description),
        _meta("property", "og:url", preview.page_url),
        _meta("property", "og:image", preview.image_url),
        _meta("name", "twitter:card", "summary"),
        _meta("name", "twitter:title", preview.title),
        _meta("name", "twitter:description", preview.description),
        _meta("name", "twitter:image", preview.image_url),
        "  </head>\n",
        "  <body>\n",
        f"    <h1>{title}</h1>\n",
        f"    <p>{html.escape(preview.description)}</p>\n",
        "  </body>\n",
        "</html>\n",
    ]
    return "".join(parts)
