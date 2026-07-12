"""OpenPlural v0.1 export mapping.

Sheaf is a founding adopter of the OpenPlural data standard
(https://github.com/skylartaylor/openplural). This module is a *pure*
transform: it takes the native Article-20 export dict that
``sheaf/api/v1/export.py::export_all`` already produces (version "2")
and reshapes it into an OpenPlural v0.1 envelope. It performs no DB
access and no decryption - everything it needs is already plaintext in
the native dict.

The mapping follows the Sheaf card in ``../openplural/adopt.md``. Every
field Sheaf has that OpenPlural v0.1 can model goes into a core record;
everything else is preserved losslessly under the namespaced
``extensions.sheaf`` key so a round-trip back into Sheaf restores it and
another app can at least carry it forward. See ``docs/OPENPLURAL.md``
for the full per-field rationale and the running implementation log.

Two delivery shapes share this builder:

* The sync ``GET /v1/export?format=openplural`` path emits a single
  JSON document with *uri-only* assets (avatar URLs, no bytes) and a
  top-level ``asset_uri_only`` warning.
* The async ``.openplural.zip`` bundle additionally writes the blobs to
  ``assets/<key>`` and records the in-zip path under each asset's
  ``extensions.sheaf.bundle_path`` (the official bundle-path convention
  is still pending upstream issue #9; until it lands we keep ``uri``
  present so the document stays spec-valid and stash our pointer in our
  own namespace).
"""

from __future__ import annotations

import uuid

from sheaf import __version__

# What this module targets / advertises.
OPENPLURAL_VERSION = "0.1"
# Bumped independently of the Sheaf app version when the *mapping* logic
# changes in a way worth tracking; surfaces as producer.exporter_version.
OPENPLURAL_IMPL_VERSION = "0.1.0"
APP_ID = "sheaf"
APP_NAME = "Sheaf"
# The reverse-DNS-free registered namespace key Sheaf owns in the spec.
EXT_NS = "sheaf"

# Stable namespace for deterministic asset ids derived from storage keys /
# URLs, so the same blob maps to the same Asset id across exports and a
# member can reference it by id.
_ASSET_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

# Native sub-sections with no OpenPlural v0.1 core representation. Carried
# verbatim under extensions.sheaf.<key> so the round-trip is lossless and
# the importer can lift them straight back. Each is parked pending the
# matching upstream module (polls/reminders -> v0.2, etc); see the table
# in docs/OPENPLURAL.md.
_EXT_PASSTHROUGH_SECTIONS = (
    "polls",
    "reminders",
    "messages",
    "revisions",
    "watch_tokens",
    "uploaded_files",
)


def _asset_id(key: str) -> str:
    return str(uuid.uuid5(_ASSET_NS, key))


def _internal_key(url: str) -> str | None:
    """Bare storage key for a Sheaf-internal reference, else None (external).

    Thin wrapper over ``sheaf.files._to_internal_key`` (local import to
    keep this module's import graph light and DB-free)."""
    from sheaf.files import _to_internal_key

    return _to_internal_key(url)


def _birthday(raw: str | None) -> dict | None:
    """Map Sheaf's ``"MM-DD"`` / ``"YYYY-MM-DD"`` birthday string to an
    OpenPlural precision-aware Birthday sub-record."""
    if not raw:
        return None
    parts = raw.split("-")
    if len(parts) == 3:
        return {"value": raw, "precision": "day", "year_visible": True}
    if len(parts) == 2:
        # Stored without a year - represent as a year-less month/day.
        return {"value": raw, "precision": "month_day", "year_visible": False}
    # Anything else: hand it across opaquely rather than dropping it.
    return {"value": raw, "precision": "unknown", "year_visible": False}


class _AssetTable:
    """Collects unique assets referenced across the export.

    Keyed by storage key / URL so each distinct blob yields one Asset
    with a deterministic id. ``include_bytes`` only affects whether a
    ``bundle_path`` pointer is recorded; the builder caller is what
    actually writes the blob into the zip.
    """

    def __init__(self, *, include_bytes: bool) -> None:
        self._by_key: dict[str, dict] = {}
        self._include_bytes = include_bytes
        self.uri_only_used = False

    def ref(self, url: str | None, *, kind: str) -> str | None:
        """Register an asset for ``url`` and return its asset id."""
        if not url:
            return None
        existing = self._by_key.get(url)
        if existing is not None:
            return existing["id"]
        asset: dict = {
            "id": _asset_id(url),
            "kind": kind,
            "uri": url,
        }
        # Only Sheaf-internal references have bytes we can bundle; an
        # external CDN URL (Gravatar, dicebear, a user-typed link) stays
        # uri-only in both delivery shapes.
        internal_key = _internal_key(url)
        if self._include_bytes and internal_key is not None:
            # The builder writes the blob under this path; until the spec
            # ratifies a bundle-path convention (issue #9) it lives in our
            # namespace and the importer reads it from here. The path keys
            # on the bare storage key so the importer's _to_internal_key
            # of the (unchanged) uri recovers the same key.
            asset.setdefault("extensions", {})[EXT_NS] = {
                "bundle_path": f"assets/{internal_key}",
                "storage_key": internal_key,
            }
        else:
            self.uri_only_used = True
        self._by_key[url] = asset
        return asset["id"]

    def as_list(self) -> list[dict]:
        return list(self._by_key.values())


def build_envelope(
    native: dict,
    *,
    exported_at: str,
    app_version: str | None = None,
    inherited_lineage: list | None = None,
    include_asset_bytes: bool = False,
) -> dict:
    """Transform a native export dict into an OpenPlural v0.1 envelope.

    ``exported_at`` is an ISO-8601 UTC timestamp supplied by the caller
    (this module takes no clock). ``inherited_lineage`` is any
    ``extensions.sheaf.lineage`` carried in from a prior import, so the
    file's journey accumulates across round-trips (forward-compat with
    upstream issue #7). ``include_asset_bytes`` is set by the bundle
    builder so assets carry an in-zip ``bundle_path``.
    """
    app_version = app_version or __version__
    assets = _AssetTable(include_bytes=include_asset_bytes)
    warnings: list[dict] = []

    systems: list[dict] = []
    members: list[dict] = []
    groups: list[dict] = []
    group_memberships: list[dict] = []
    taxonomy_terms: list[dict] = []
    taxonomy_assignments: list[dict] = []
    custom_fields: list[dict] = []
    custom_field_values: list[dict] = []
    front_periods: list[dict] = []
    notes: list[dict] = []
    board_posts: list[dict] = []

    # --- System -----------------------------------------------------------
    sys_data = native.get("system")
    if isinstance(sys_data, dict):
        sys_ext = {
            "note": sys_data.get("note"),
            "date_format": sys_data.get("date_format"),
            "timezone": sys_data.get("timezone"),
            "replace_fronts_default": sys_data.get("replace_fronts_default"),
            "coalesce_contiguous_fronts": sys_data.get("coalesce_contiguous_fronts"),
            "delete_confirmation": sys_data.get("delete_confirmation"),
            "safety": sys_data.get("safety"),
            "retention": sys_data.get("retention"),
        }
        systems.append(
            {
                "id": sys_data.get("id"),
                "name": sys_data.get("name"),
                "description": sys_data.get("description"),
                "tag": sys_data.get("tag"),
                "color": sys_data.get("color"),
                "avatar_asset_id": assets.ref(sys_data.get("avatar_url"), kind="avatar"),
                "privacy": _privacy_obj(sys_data.get("privacy")),
                "extensions": {EXT_NS: _prune(sys_ext)},
            }
        )

    # --- Members ----------------------------------------------------------
    for m in native.get("members", []):
        if not isinstance(m, dict):
            continue
        source_refs = []
        if m.get("pluralkit_id"):
            source_refs.append(
                {"app": "pluralkit", "collection": "members", "id": m["pluralkit_id"]}
            )
        m_ext = {
            "note": m.get("note"),
            "emoji": m.get("emoji"),
            "quick_switch_pin": m.get("quick_switch_pin"),
            "notify_on_front_global": m.get("notify_on_front_global"),
            "notify_on_front_self": m.get("notify_on_front_self"),
            "notify_on_front_member_ids": m.get("notify_on_front_member_ids"),
        }
        members.append(
            {
                "id": m.get("id"),
                "name": m.get("name"),
                "display_name": m.get("display_name"),
                "description": m.get("description"),
                "pronouns": m.get("pronouns"),
                "color": m.get("color"),
                "birthday": _birthday(m.get("birthday")),
                "is_custom_front": bool(m.get("is_custom_front")),
                "archived": bool(m.get("archived_at")),
                "avatar_asset_id": assets.ref(m.get("avatar_url"), kind="avatar"),
                "banner_asset_id": assets.ref(m.get("banner_url"), kind="banner"),
                "privacy": _privacy_obj(m.get("privacy")),
                "created_at": m.get("created_at"),
                "source_refs": source_refs,
                "extensions": {EXT_NS: _prune(m_ext)},
            }
        )

    # --- Groups (+ normalized memberships) --------------------------------
    for g in native.get("groups", []):
        if not isinstance(g, dict):
            continue
        groups.append(
            {
                "id": g.get("id"),
                "name": g.get("name"),
                "description": g.get("description"),
                "color": g.get("color"),
                "parent_group_id": g.get("parent_id"),
            }
        )
        for mid in g.get("member_ids", []) or []:
            group_memberships.append({"group_id": g.get("id"), "member_id": mid})

    # --- Tags -> taxonomy (kind=tag) + assignments ------------------------
    for t in native.get("tags", []):
        if not isinstance(t, dict):
            continue
        taxonomy_terms.append(
            {"id": t.get("id"), "kind": "tag", "name": t.get("name"), "color": t.get("color")}
        )
        for mid in t.get("member_ids", []) or []:
            taxonomy_assignments.append(
                {"term_id": t.get("id"), "subject_type": "member", "subject_id": mid}
            )

    # --- Custom fields ----------------------------------------------------
    for fd in native.get("custom_fields", []):
        if not isinstance(fd, dict):
            continue
        custom_fields.append(
            {
                "id": fd.get("id"),
                "name": fd.get("name"),
                "field_type": fd.get("field_type"),
                "options": fd.get("options"),
                "sort_order": fd.get("order"),
                "privacy": _privacy_obj(fd.get("privacy")),
            }
        )
        for v in fd.get("values", []) or []:
            if not isinstance(v, dict):
                continue
            custom_field_values.append(
                {
                    "field_id": fd.get("id"),
                    "subject_type": "member",
                    "subject_id": v.get("member_id"),
                    "value": v.get("value"),
                }
            )

    # --- Fronts -> front_periods ------------------------------------------
    for f in native.get("fronts", []):
        if not isinstance(f, dict):
            continue
        front_periods.append(
            {
                "id": f.get("id"),
                "started_at": f.get("started_at"),
                "ended_at": f.get("ended_at"),
                "assignments": [
                    {"member_id": mid, "front_role": "member"}
                    for mid in f.get("member_ids", []) or []
                ],
                "status": f.get("custom_status"),
            }
        )

    # --- Journals -> notes ------------------------------------------------
    for j in native.get("journals", []):
        if not isinstance(j, dict):
            continue
        attachment_ids = [
            assets.ref(k, kind="image") for k in (j.get("image_keys") or []) if k
        ]
        notes.append(
            {
                "id": j.get("id"),
                "title": j.get("title"),
                "body": j.get("body"),
                "visibility": _privacy(j.get("visibility")),
                "author_member_ids": j.get("author_member_ids") or [],
                "created_at": j.get("created_at"),
                "updated_at": j.get("updated_at"),
                "attachment_asset_ids": [a for a in attachment_ids if a],
                "extensions": {
                    EXT_NS: _prune(
                        {
                            "member_id": j.get("member_id"),
                            "author_member_names": j.get("author_member_names"),
                        }
                    )
                },
            }
        )

    # --- Messages -> boards.posts module ----------------------------------
    for msg in native.get("messages", []):
        if not isinstance(msg, dict):
            continue
        board_posts.append(
            {
                "id": msg.get("id"),
                "target_member_id": msg.get("board_member_id"),
                "author_member_id": msg.get("author_member_id"),
                "body": msg.get("body"),
                "created_at": msg.get("created_at"),
                "updated_at": msg.get("updated_at"),
                "extensions": {
                    EXT_NS: _prune(
                        {
                            "board_kind": msg.get("board_kind"),
                            # Reply pointer parks here until BoardPost.parent_post_id
                            # lands upstream (issue #2).
                            "parent_message_id": msg.get("parent_message_id"),
                        }
                    )
                },
            }
        )

    # --- File-level extensions: passthrough sections + lineage ------------
    file_ext: dict = {}
    for section in _EXT_PASSTHROUGH_SECTIONS:
        rows = native.get(section)
        if rows:
            file_ext[section] = rows

    lineage = list(inherited_lineage or [])
    lineage.append(
        {
            "app": APP_ID,
            "app_version": app_version,
            "exporter_version": OPENPLURAL_IMPL_VERSION,
            "exported_at": exported_at,
        }
    )
    file_ext["lineage"] = lineage

    # --- Capabilities -----------------------------------------------------
    modules: list[str] = []
    if board_posts:
        modules.append("boards")

    if assets.uri_only_used:
        warnings.append(
            {
                "level": "info",
                "code": "asset_uri_only",
                "message": (
                    "Assets are referenced by URL only; export with images "
                    "(the .openplural.zip bundle) to include the binary blobs."
                ),
            }
        )

    envelope: dict = {
        "openplural_version": OPENPLURAL_VERSION,
        "exported_at": exported_at,
        "producer": {
            "app": APP_NAME,
            "app_id": APP_ID,
            "app_version": app_version,
            "exporter_version": OPENPLURAL_IMPL_VERSION,
        },
        "capabilities": {"modules": modules},
        "systems": systems,
        "members": members,
        "groups": groups,
        "group_memberships": group_memberships,
        "taxonomy_terms": taxonomy_terms,
        "taxonomy_assignments": taxonomy_assignments,
        "custom_fields": custom_fields,
        "custom_field_values": custom_field_values,
        "front_periods": front_periods,
        "notes": notes,
        "assets": assets.as_list(),
        "extensions": {EXT_NS: file_ext},
        "warnings": warnings,
    }
    if board_posts:
        envelope["boards"] = {"posts": board_posts}

    # Re-merge any preserved residual from a prior foreign import (the
    # baseline passthrough tier: file-level extensions + whole un-consumed
    # sections). Sheaf stores this encrypted on the system; the native
    # export decrypts it into `system.openplural_archive` as a plain dict.
    preserved = sys_data.get("openplural_archive") if isinstance(sys_data, dict) else None
    if isinstance(preserved, dict) and preserved:
        _merge_preserved(envelope, preserved)
    return envelope


def _merge_preserved(envelope: dict, preserved: dict) -> None:
    """Fold a preserved import-residual back into a freshly-built envelope."""
    foreign_ext = preserved.get("extensions")
    if isinstance(foreign_ext, dict):
        # Sheaf's own namespace wins on a clash; foreign namespaces ride alongside.
        merged = dict(foreign_ext)
        merged.update(envelope["extensions"])
        envelope["extensions"] = merged

    modules = envelope["capabilities"]["modules"]
    for key in ("chat", "relationships"):
        val = preserved.get(key)
        if val:
            envelope[key] = val
            if key not in modules:
                modules.append(key)

    for key in ("front_events", "front_comments"):
        val = preserved.get(key)
        if val:
            envelope[key] = val

    extra_terms = preserved.get("taxonomy_terms")
    if isinstance(extra_terms, list) and extra_terms:
        envelope["taxonomy_terms"] = list(envelope["taxonomy_terms"]) + extra_terms
    extra_assignments = preserved.get("taxonomy_assignments")
    if isinstance(extra_assignments, list) and extra_assignments:
        envelope["taxonomy_assignments"] = (
            list(envelope["taxonomy_assignments"]) + extra_assignments
        )


_VALID_VISIBILITY = {"public", "friends", "private", "trusted", "unknown"}


def _privacy(val: str | None) -> str:
    """Sheaf privacy/visibility -> OpenPlural conservative bucket.

    Sheaf's PrivacyLevel (public/friends/private) maps 1:1 onto the
    OpenPlural visibility vocabulary; anything unrecognised rounds to
    the strictest-safe ``unknown``.
    """
    if val in _VALID_VISIBILITY:
        return val
    return "unknown"


def _privacy_obj(val: str | None) -> dict:
    """Wrap a Sheaf privacy bucket in the OpenPlural Privacy *object*
    (``{"visibility": ...}``), which is the spec shape for system / member /
    custom-field privacy. Sheaf has no richer raw source detail to carry, so
    the optional ``source`` key is omitted. (Note/journal ``visibility`` is a
    plain string in the spec, not this object - those keep using ``_privacy``
    directly.)"""
    return {"visibility": _privacy(val)}


def _prune(d: dict) -> dict:
    """Drop None-valued keys so extensions stay tidy (and absent != null)."""
    return {k: v for k, v in d.items() if v is not None}
