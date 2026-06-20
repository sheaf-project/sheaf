"""OpenPlural v0.1 import: envelope -> native Sheaf shape.

Sheaf's OpenPlural importer is deliberately thin. Rather than re-walk
every record type, it *translates* an OpenPlural v0.1 envelope back into
the native Article-20 export dict (version "2") that
``sheaf_import.run_import`` already consumes, then delegates. Every
mandatory guard (member cap, safe-JSON, decompressed-size bound, avatar
normalisation, internal-image stripping, business caps, fresh UUIDs,
tenant scoping) therefore lives in the native importer and cannot drift
per-format. This module is the inverse of ``openplural_export`` and is
verified against it by the round-trip test.

Two payload shapes:

* A bare ``.json`` document -> translate, hand to ``sheaf_import``.
* An ``.openplural.zip`` bundle (``openplural.json`` + ``assets/<key>``)
  -> translate, then hand a ``ParsedArchive`` (with ``asset_prefix``
  ``"assets/"``) to ``sheaf_archive_import`` so the blobs are restored.

See ``docs/OPENPLURAL.md`` for the field-by-field mapping and the list
of things that round-trip via ``extensions.sheaf.*``.
"""

from __future__ import annotations

import asyncio
import io
import zipfile

from sheaf.services.import_parsing import ImportPayloadError, expect_dict, safe_json_loads
from sheaf.services.openplural_export import EXT_NS

# Versions this importer understands. The spec mandates rejecting an
# unknown openplural_version rather than silently part-importing it.
SUPPORTED_VERSIONS = {"0.1"}

# Matches the native archive's decompressed-size caps (DEFLATE reaches
# ~1000:1, so the 100MB compressed upload cap alone does not bound memory).
_MAX_JSON_DECOMPRESSED = 256 * 1024 * 1024
_MAX_ASSET_DECOMPRESSED = 100 * 1024 * 1024
_BUNDLE_JSON_NAME = "openplural.json"
_ASSET_PREFIX = "assets/"

# zip local-file-header magic; lets the runner sniff bundle vs bare JSON.
_ZIP_MAGIC = b"PK\x03\x04"


def looks_like_zip(blob: bytes) -> bool:
    return blob[:4] == _ZIP_MAGIC


def _check_version(envelope: dict) -> None:
    ver = envelope.get("openplural_version")
    if ver not in SUPPORTED_VERSIONS:
        raise ImportPayloadError(
            f"unsupported openplural_version {ver!r} "
            f"(this build understands {sorted(SUPPORTED_VERSIONS)})"
        )


def _ext(obj: dict) -> dict:
    """The ``extensions.sheaf`` sub-dict of a record, or empty."""
    if not isinstance(obj, dict):
        return {}
    ext = obj.get("extensions")
    if not isinstance(ext, dict):
        return {}
    sheaf = ext.get(EXT_NS)
    return sheaf if isinstance(sheaf, dict) else {}


def _birthday_to_native(b: object) -> str | None:
    """OpenPlural Birthday sub-record -> Sheaf's flat ``MM-DD`` / ``YYYY-MM-DD``."""
    if isinstance(b, str):
        return b
    if isinstance(b, dict):
        val = b.get("value")
        return val if isinstance(val, str) else None
    return None


class _AssetMap:
    """asset id -> native image reference, built from the envelope's assets.

    For a bare-JSON import the reference is the asset's ``uri`` (an
    external URL the native importer treats like any other). For a bundle
    it is still the ``uri`` (a ``/v1/files/<key>`` form), and the bare
    storage key parsed from ``bundle_path`` is what the archive restore
    keys its blobs by.
    """

    def __init__(self, envelope: dict) -> None:
        self.by_id: dict[str, str] = {}
        self.bundle_keys: set[str] = set()
        for a in envelope.get("assets", []) or []:
            if not isinstance(a, dict):
                continue
            aid = a.get("id")
            if not aid:
                continue
            uri = a.get("uri")
            bundle_path = _ext(a).get("bundle_path")
            if isinstance(uri, str) and uri:
                self.by_id[aid] = uri
            if isinstance(bundle_path, str) and bundle_path.startswith(_ASSET_PREFIX):
                self.bundle_keys.add(bundle_path[len(_ASSET_PREFIX):])

    def url(self, asset_id: object) -> str | None:
        if isinstance(asset_id, str):
            return self.by_id.get(asset_id)
        return None


def to_native(envelope: dict) -> dict:
    """Translate an OpenPlural v0.1 envelope into the native export dict.

    Pure transform: no DB, no IO. Mirrors ``openplural_export.build_envelope``.
    """
    _check_version(envelope)
    assets = _AssetMap(envelope)

    native: dict = {"version": "2"}

    # --- System -----------------------------------------------------------
    systems = envelope.get("systems") or []
    sys_in = systems[0] if systems and isinstance(systems[0], dict) else None
    if sys_in is not None:
        ext = _ext(sys_in)
        native["system"] = {
            "id": sys_in.get("id"),
            "name": sys_in.get("name"),
            "description": sys_in.get("description"),
            "note": ext.get("note"),
            "tag": sys_in.get("tag"),
            "avatar_url": assets.url(sys_in.get("avatar_asset_id")),
            "color": sys_in.get("color"),
            "privacy": sys_in.get("privacy"),
            "date_format": ext.get("date_format"),
            "replace_fronts_default": ext.get("replace_fronts_default"),
            "coalesce_contiguous_fronts": ext.get("coalesce_contiguous_fronts"),
            "delete_confirmation": ext.get("delete_confirmation"),
            "safety": ext.get("safety"),
            "retention": ext.get("retention"),
        }
    else:
        native["system"] = None

    # --- Members ----------------------------------------------------------
    members: list[dict] = []
    for m in envelope.get("members", []) or []:
        if not isinstance(m, dict):
            continue
        ext = _ext(m)
        members.append(
            {
                "id": m.get("id"),
                "name": m.get("name"),
                "display_name": m.get("display_name"),
                "description": m.get("description"),
                "pronouns": m.get("pronouns"),
                "avatar_url": assets.url(m.get("avatar_asset_id")),
                "banner_url": assets.url(m.get("banner_asset_id")),
                "color": m.get("color"),
                "birthday": _birthday_to_native(m.get("birthday")),
                "pluralkit_id": _pluralkit_id(m.get("source_refs")),
                "emoji": ext.get("emoji"),
                "is_custom_front": bool(m.get("is_custom_front")),
                "privacy": m.get("privacy"),
                "note": ext.get("note"),
                "quick_switch_pin": ext.get("quick_switch_pin"),
                "notify_on_front_global": ext.get("notify_on_front_global"),
                "notify_on_front_self": ext.get("notify_on_front_self"),
                "notify_on_front_member_ids": ext.get("notify_on_front_member_ids"),
                "created_at": m.get("created_at"),
            }
        )
    native["members"] = members

    # --- Groups (+ denormalize memberships) -------------------------------
    group_members = _group_index(envelope.get("group_memberships"))
    groups: list[dict] = []
    for g in envelope.get("groups", []) or []:
        if not isinstance(g, dict):
            continue
        groups.append(
            {
                "id": g.get("id"),
                "name": g.get("name"),
                "description": g.get("description"),
                "color": g.get("color"),
                "parent_id": g.get("parent_group_id"),
                "member_ids": group_members.get(g.get("id"), []),
            }
        )
    native["groups"] = groups

    # --- Taxonomy(kind=tag) -> tags (+ denormalize assignments) -----------
    tag_members = _assignment_index(envelope.get("taxonomy_assignments"))
    tags: list[dict] = []
    for t in envelope.get("taxonomy_terms", []) or []:
        if not isinstance(t, dict) or t.get("kind") != "tag":
            continue
        tags.append(
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "color": t.get("color"),
                "member_ids": tag_members.get(t.get("id"), []),
            }
        )
    native["tags"] = tags

    # --- Custom fields (+ values) -----------------------------------------
    field_values = _field_value_index(envelope.get("custom_field_values"))
    fields: list[dict] = []
    for fd in envelope.get("custom_fields", []) or []:
        if not isinstance(fd, dict):
            continue
        fields.append(
            {
                "id": fd.get("id"),
                "name": fd.get("name"),
                "field_type": fd.get("field_type"),
                "options": fd.get("options"),
                "order": fd.get("sort_order"),
                "privacy": fd.get("privacy"),
                "values": field_values.get(fd.get("id"), []),
            }
        )
    native["custom_fields"] = fields

    # --- Front periods -> fronts ------------------------------------------
    fronts: list[dict] = []
    for f in envelope.get("front_periods", []) or []:
        if not isinstance(f, dict):
            continue
        member_ids = [
            a.get("member_id")
            for a in (f.get("assignments") or [])
            if isinstance(a, dict) and a.get("member_id")
        ]
        fronts.append(
            {
                "id": f.get("id"),
                "started_at": f.get("started_at"),
                "ended_at": f.get("ended_at"),
                "member_ids": member_ids,
                "custom_status": f.get("status"),
            }
        )
    native["fronts"] = fronts

    # --- Notes -> journals ------------------------------------------------
    journals: list[dict] = []
    for n in envelope.get("notes", []) or []:
        if not isinstance(n, dict):
            continue
        ext = _ext(n)
        image_keys = [
            assets.url(aid) for aid in (n.get("attachment_asset_ids") or [])
        ]
        journals.append(
            {
                "id": n.get("id"),
                "member_id": ext.get("member_id"),
                "title": n.get("title"),
                "body": n.get("body"),
                "visibility": n.get("visibility"),
                "author_member_ids": n.get("author_member_ids") or [],
                "author_member_names": ext.get("author_member_names"),
                "image_keys": [k for k in image_keys if k],
                "created_at": n.get("created_at"),
                "updated_at": n.get("updated_at"),
            }
        )
    native["journals"] = journals

    # --- boards.posts -> messages -----------------------------------------
    messages: list[dict] = []
    boards = envelope.get("boards")
    if isinstance(boards, dict):
        for p in boards.get("posts", []) or []:
            if not isinstance(p, dict):
                continue
            ext = _ext(p)
            messages.append(
                {
                    "id": p.get("id"),
                    "board_kind": ext.get("board_kind"),
                    "board_member_id": p.get("target_member_id"),
                    "author_member_id": p.get("author_member_id"),
                    "parent_message_id": ext.get("parent_message_id"),
                    "body": p.get("body"),
                    "created_at": p.get("created_at"),
                    "updated_at": p.get("updated_at"),
                }
            )

    # --- File-level passthrough sections ----------------------------------
    file_ext = _file_extensions(envelope)
    # messages may come from boards module OR the passthrough section; the
    # boards module wins when present, else fall back to the passthrough.
    native["messages"] = messages or (file_ext.get("messages") or [])
    native["revisions"] = file_ext.get("revisions") or []
    native["watch_tokens"] = file_ext.get("watch_tokens") or []
    native["uploaded_files"] = file_ext.get("uploaded_files") or []
    native["reminders"] = file_ext.get("reminders") or []
    native["polls"] = file_ext.get("polls") or []

    return native


def inherited_lineage(envelope: dict) -> list:
    """The ``extensions.sheaf.lineage`` array carried in, or empty."""
    lineage = _file_extensions(envelope).get("lineage")
    return lineage if isinstance(lineage, list) else []


# --- index helpers -----------------------------------------------------------


def _file_extensions(envelope: dict) -> dict:
    ext = envelope.get("extensions")
    if not isinstance(ext, dict):
        return {}
    sheaf = ext.get(EXT_NS)
    return sheaf if isinstance(sheaf, dict) else {}


def _pluralkit_id(source_refs: object) -> str | None:
    if not isinstance(source_refs, list):
        return None
    for ref in source_refs:
        if isinstance(ref, dict) and ref.get("app") == "pluralkit" and ref.get("id"):
            return ref["id"]
    return None


def _group_index(memberships: object) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in memberships or []:
        if isinstance(row, dict) and row.get("group_id") and row.get("member_id"):
            out.setdefault(row["group_id"], []).append(row["member_id"])
    return out


def _assignment_index(assignments: object) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in assignments or []:
        if (
            isinstance(row, dict)
            and row.get("subject_type") == "member"
            and row.get("term_id")
            and row.get("subject_id")
        ):
            out.setdefault(row["term_id"], []).append(row["subject_id"])
    return out


def _field_value_index(values: object) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in values or []:
        if (
            isinstance(row, dict)
            and row.get("subject_type") == "member"
            and row.get("field_id")
            and row.get("subject_id")
        ):
            out.setdefault(row["field_id"], []).append(
                {"member_id": row["subject_id"], "value": row.get("value")}
            )
    return out


# --- parse entrypoints -------------------------------------------------------


def parse_json(blob: bytes) -> dict:
    """Parse + version-check a bare OpenPlural JSON document."""
    envelope = expect_dict(safe_json_loads(blob), descriptor="OpenPlural export")
    _check_version(envelope)
    return envelope


def parse_bundle(blob: bytes):
    """Open an .openplural.zip, validate, and return a translated
    ``ParsedArchive`` ready for ``sheaf_archive_import.run_import``.

    Returns ``(ParsedArchive, envelope)``: the archive carries the
    native-translated ``data`` and an ``assets/`` ``asset_prefix``; the
    envelope is handed back for lineage extraction.
    """
    from sheaf.services.sheaf_archive_import import ParsedArchive

    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ImportPayloadError("file is not a valid zip archive") from exc

    names = set(zf.namelist())
    if _BUNDLE_JSON_NAME not in names:
        raise ImportPayloadError(
            f"OpenPlural bundle must contain {_BUNDLE_JSON_NAME} "
            "(is this an .openplural.zip export?)"
        )
    if zf.getinfo(_BUNDLE_JSON_NAME).file_size > _MAX_JSON_DECOMPRESSED:
        raise ImportPayloadError(
            f"{_BUNDLE_JSON_NAME} decompresses to more than "
            f"{_MAX_JSON_DECOMPRESSED // (1024 * 1024)}MB; refusing to parse"
        )

    envelope = expect_dict(
        safe_json_loads(zf.read(_BUNDLE_JSON_NAME)), descriptor="OpenPlural bundle"
    )
    _check_version(envelope)

    native = to_native(envelope)
    bundle_keys = {
        n.removeprefix(_ASSET_PREFIX)
        for n in names
        if n.startswith(_ASSET_PREFIX) and not n.endswith("/")
    }
    parsed = ParsedArchive(
        data=native, zf=zf, image_keys=bundle_keys, asset_prefix=_ASSET_PREFIX
    )
    return parsed, envelope


_parse_semaphore: asyncio.Semaphore | None = None


def _get_parse_semaphore() -> asyncio.Semaphore:
    global _parse_semaphore
    if _parse_semaphore is None:
        _parse_semaphore = asyncio.Semaphore(2)
    return _parse_semaphore


async def parse_bundle_async(blob: bytes):
    async with _get_parse_semaphore():
        return await asyncio.to_thread(parse_bundle, blob)
