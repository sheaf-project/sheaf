"""The server image choke point must over-recognize relative to the client.

``iter_markdown_images`` is the single function every image rail funnels
through (validation, SSRF checks, stripping). The web renderer is
``react-markdown`` + ``remark-gfm``, which accepts a GFM superset of
CommonMark. If the server parsed a narrower grammar than the client, an image
the client renders could slip past every rail while still displaying to
viewers - a bypass-the-rail hole. So for any document, the set of images the
server extracts MUST be a superset of the set the client renders.

These run in-process without touching the live server: they exercise only
``sheaf.markdown_images``.

Source of truth for the client set: remark-gfm semantics
(https://github.com/remarkjs/remark-gfm). Each row hardcodes the image URLs
remark-gfm would render for that document. A full JS round-trip is not
required - the GFM constructs exercised here (footnote definitions, tables,
task lists, strikethrough, autolinks, reference/collapsed/shortcut images)
have stable, well-specified rendering, so the expected client set is
enumerated by hand and documented per row.
"""

import pytest

from sheaf.markdown_images import iter_markdown_images


def _server_set(text: str) -> set[str]:
    return {image.url for image in iter_markdown_images(text)}


# (id, markdown source, client-rendered image URLs per remark-gfm).
#
# The only construct CommonMark hides is the footnote definition: it reads
# ``[^1]: ![a](/x.png)`` as a link reference definition and swallows the image
# as a URL string, while remark-gfm renders it. The remaining rows are images
# living inside other GFM block constructs; the server already detects them
# because the inline image rule fires inside their paragraph content, so they
# double as regression guards proving those constructs never fall BELOW the
# client set.
CASES = [
    # Footnote definition, inline single-line form.
    (
        "footnote_inline",
        "body[^1]\n\n[^1]: ![alt](/footnote-inline.png)",
        {"/footnote-inline.png"},
    ),
    # Footnote definition, multi-line (indented continuation) form.
    (
        "footnote_multiline",
        "body[^1]\n\n[^1]: intro line\n    ![alt](/footnote-multiline.png)",
        {"/footnote-multiline.png"},
    ),
    # Footnote definition with the image among surrounding prose.
    (
        "footnote_among_prose",
        "see[^note] below\n\n[^note]: look ![alt](/footnote-prose.png) here",
        {"/footnote-prose.png"},
    ),
    # Image inside a GFM table cell.
    (
        "table_cell",
        "| head |\n| --- |\n| ![alt](/table-cell.png) |",
        {"/table-cell.png"},
    ),
    # Image inside a GFM task list item.
    (
        "task_list_item",
        "- [ ] ![alt](/task-list.png)",
        {"/task-list.png"},
    ),
    # Image wrapped in a GFM strikethrough run.
    (
        "strikethrough",
        "~~![alt](/strikethrough.png)~~",
        {"/strikethrough.png"},
    ),
    # Image alongside a GFM autolink (the autolink is a link, not an image;
    # only the image counts toward the rendered image set).
    (
        "autolink_context",
        "<https://example.com/page> then ![alt](/autolink.png)",
        {"/autolink.png"},
    ),
    # Full reference image: ![alt][label] with a matching definition.
    (
        "reference_image",
        "![alt][ref]\n\n[ref]: /reference.png",
        {"/reference.png"},
    ),
    # Collapsed reference image: ![label][] with a matching definition.
    (
        "collapsed_reference_image",
        "![ref][]\n\n[ref]: /collapsed.png",
        {"/collapsed.png"},
    ),
    # Shortcut reference image: ![label] with a matching definition.
    (
        "shortcut_reference_image",
        "![ref]\n\n[ref]: /shortcut.png",
        {"/shortcut.png"},
    ),
]


@pytest.mark.parametrize(
    ("markdown", "client_rendered"),
    [pytest.param(text, expected, id=name) for name, text, expected in CASES],
)
def test_server_image_set_is_superset_of_client(
    markdown: str, client_rendered: set[str]
) -> None:
    server = _server_set(markdown)
    missing = client_rendered - server
    assert not missing, (
        f"server image set {server} is missing client-rendered images "
        f"{missing}; these would bypass the image rails while still rendering"
    )
    # Restate the invariant directly for readability of failures.
    assert server >= client_rendered


def test_footnote_image_regresses_without_the_plugin() -> None:
    """Guard the specific bypass: a footnote-defined image must be seen.

    Without the footnote extension the server reads this as a link reference
    definition and yields no image, so this row is the canary for the fix.
    """
    text = "ref[^1]\n\n[^1]: ![alt](/canary.png)"
    assert "/canary.png" in _server_set(text)
