"""CommonMark-aware discovery and rewriting of Markdown image nodes.

The API stores Markdown source, so rendering it to HTML just to enforce an
image policy would destroy the user's formatting.  ``markdown-it-py`` gives us
the same CommonMark image grammar used by the web renderer.  A small wrapper
around its image rule records source spans, allowing targeted edits while
leaving prose, links, code spans, and code blocks byte-for-byte unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from markdown_it import MarkdownIt
from markdown_it.rules_inline.image import image as _parse_image
from markdown_it.rules_inline.state_inline import StateInline


@dataclass(frozen=True)
class MarkdownImage:
    """A rendered image and its location in the original Markdown source."""

    start: int
    end: int
    url: str
    alt: str
    title: str | None


def _image_with_source_span(state: StateInline, silent: bool) -> bool:
    start = state.pos
    token_count = len(state.tokens)
    matched = _parse_image(state, silent)
    if matched and not silent and len(state.tokens) > token_count:
        state.tokens[-1].meta["source_span"] = (start, state.pos)
    return matched


_MARKDOWN = MarkdownIt("commonmark", {"store_labels": True})
_MARKDOWN.inline.ruler.at("image", _image_with_source_span)


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    index = 0
    while index < len(text):
        if text[index] == "\r":
            index += 2 if text[index : index + 2] == "\r\n" else 1
            offsets.append(index)
        elif text[index] == "\n":
            index += 1
            offsets.append(index)
        else:
            index += 1
    offsets.append(len(text))
    return offsets


def _inline_source_map(
    text: str,
    content: str,
    line_map: list[int],
    offsets: list[int],
) -> list[int] | None:
    """Map block-normalized inline content positions to original offsets.

    Block parsing removes list/quote/heading markers before inline parsing.
    Each remaining content line is otherwise a literal substring of its
    source line, so align line-by-line and retain an offset for every content
    character. Returning ``None`` lets the caller take a conservative raw
    block fallback for an unexpected parser normalization.
    """

    raw_start = offsets[line_map[0]]
    raw_end = offsets[line_map[1]]
    raw = text[raw_start:raw_end]
    mapping: list[int] = []
    raw_cursor = 0
    for piece in content.splitlines(keepends=True):
        has_newline = piece.endswith("\n")
        body = piece[:-1] if has_newline else piece
        found = raw.find(body, raw_cursor)
        if found < 0:
            return None
        mapping.extend(raw_start + found + index for index in range(len(body)))
        raw_cursor = found + len(body)
        if has_newline:
            newline = next(
                (
                    index
                    for index in range(raw_cursor, len(raw))
                    if raw[index] in "\r\n"
                ),
                -1,
            )
            if newline == -1:
                return None
            mapping.append(raw_start + newline)
            raw_cursor = newline + (
                2 if raw[newline : newline + 2] == "\r\n" else 1
            )

    return mapping if len(mapping) == len(content) else None


def _image_from_token(token, start: int, end: int) -> MarkdownImage:
    return MarkdownImage(
        start=start,
        end=end,
        url=token.attrGet("src") or "",
        alt=token.content,
        title=token.attrGet("title"),
    )


def _raw_block_images(raw: str, raw_start: int, env: dict) -> Iterator[MarkdownImage]:
    """Conservative fallback when block content cannot be source-aligned."""

    tokens = []
    _MARKDOWN.inline.parse(raw, _MARKDOWN, env, tokens)
    for token in tokens:
        if token.type != "image" or "source_span" not in token.meta:
            continue
        start, end = token.meta["source_span"]
        yield _image_from_token(token, raw_start + start, raw_start + end)


def iter_markdown_images(text: str | None) -> Iterator[MarkdownImage]:
    """Yield images the CommonMark renderer recognizes, in source order."""

    if not text:
        return

    env: dict = {}
    block_tokens = _MARKDOWN.parse(text, env)
    offsets = _line_offsets(text)
    seen: set[tuple[int, int]] = set()
    for block_token in block_tokens:
        if block_token.type != "inline" or not block_token.map:
            continue
        mapping = _inline_source_map(
            text,
            block_token.content,
            block_token.map,
            offsets,
        )
        if mapping is None:
            raw_start = offsets[block_token.map[0]]
            raw_end = offsets[block_token.map[1]]
            images = _raw_block_images(text[raw_start:raw_end], raw_start, env)
        else:
            mapped_images = []
            for token in block_token.children or []:
                if token.type != "image" or "source_span" not in token.meta:
                    continue
                relative_start, relative_end = token.meta["source_span"]
                if relative_end <= relative_start:
                    continue
                mapped_images.append(
                    _image_from_token(
                        token,
                        mapping[relative_start],
                        mapping[relative_end - 1] + 1,
                    )
                )
            images = iter(mapped_images)

        for image in images:
            span = (image.start, image.end)
            if span not in seen:
                seen.add(span)
                yield image


def render_markdown_image(image: MarkdownImage, url: str) -> str:
    """Return equivalent inline Markdown with a replacement destination."""

    # ``alt`` is the parser's raw source slice, not rendered text. Keeping it
    # raw preserves escapes (notably ``\]``) and balanced nested brackets.
    title = ""
    if image.title:
        escaped = image.title.replace("\\", "\\\\").replace('"', '\\"')
        title = f' "{escaped}"'
    return f"![{image.alt}]({url}{title})"


def rewrite_markdown_images(
    text: str | None,
    replace: Callable[[MarkdownImage], str | None],
) -> str | None:
    """Apply source-preserving replacements to rendered image nodes.

    ``replace`` returns a full Markdown replacement, ``""`` to remove the
    node, or ``None`` to leave the original source untouched.
    """

    if text is None:
        return None
    edits = [
        (image.start, image.end, replacement)
        for image in iter_markdown_images(text)
        if (replacement := replace(image)) is not None
    ]
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    return text
