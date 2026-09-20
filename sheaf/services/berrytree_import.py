"""BerryTree data import service.

BerryTree exports are a single JSON object with one key per section:
`members`, `front_entries`, `custom_statuses`, `folders`, `system_contexts`,
`layers`, plus a long tail of sections (`chat`, `polls`, `journal`, `places`,
`map_nodes`, ...) and a `_partial_errors` array the exporter uses to record
sections it failed to write.

EXPERIMENTAL, and deliberately narrow. Every other importer here was built
against at least two of: test data we generated ourselves by exercising every
feature of the source app, several real exports of varying vintage, or a
reference implementation to read. For BerryTree we have exactly one synthetic
sample with most sections empty, an app that has been pulled from the store,
and a server that has been down long enough that we cannot generate more. So
this importer maps only the sections the sample actually demonstrates:

    members, custom_statuses, front_entries, folders, and the main
    system_context's profile fields

Every other section is COUNTED and REPORTED, never guessed at. An export
carrying 400 journal entries produces a warning naming the number and asking
the user to get in touch, which is a bug report with a data sample attached.
Guessing at `journal[]`'s field names would instead produce a clean-looking
import that silently dropped them - the failure mode that is worst for the
user and invisible to us.

See ../sheaf-design-docs for the field-level gap notes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import blind_index, encrypt
from sheaf.encrypted_fields import (
    front_custom_status_aad,
    member_description_aad,
    member_name_aad,
)
from sheaf.models.custom_field import CustomFieldDefinition, CustomFieldValue, FieldType
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.member import Member, front_members, group_members, member_tags
from sheaf.models.system import PrivacyLevel, System
from sheaf.models.tag import Tag
from sheaf.schemas.berrytree_import import (
    BTImportOptions,
    BTImportResult,
    BTPreviewCustomFront,
    BTPreviewMember,
    BTPreviewSummary,
    BTUnsupportedSection,
)
from sheaf.schemas.custom_field import value_over_text_cap
from sheaf.services import import_limits as il
from sheaf.services.custom_fields import encrypt_field_value
from sheaf.services.import_content_dedup import (
    ContentMatchIndex,
    PairGuard,
    front_key,
    load_field_def_index,
    load_field_value_guard,
    load_front_index,
    load_group_index,
    load_group_member_guard,
    load_member_tag_guard,
    load_tag_index,
    normalize_front_interval,
)
from sheaf.services.import_dedup import (
    ImportConflictStrategy,
    candidate_key,
    count_new_members,
    load_member_match_index,
    privacy_hold_warning,
    resolve_member,
)
from sheaf.services.import_image_strip import strip_internal_image_refs_md_to_none
from sheaf.services.import_limits import ClampReport, clamp_str
from sheaf.services.import_parsing import sanitize_external_avatar_url
from sheaf.services.member_defaults import default_fronting_private
from sheaf.services.member_limits import enforce_import_member_cap

logger = logging.getLogger("sheaf.import.berrytree")

# The `schema_version` the one sample we have was written at. A different
# number is not an error - the file may well still import cleanly - but the
# user is told, because it is the single most useful thing to quote back to
# us when something looks wrong.
KNOWN_SCHEMA_VERSION = 3

# Sections BerryTree writes that this importer does not map, with the label
# used when reporting how many records were left behind. Order is the order
# they are reported in.
_UNSUPPORTED_SECTIONS: tuple[tuple[str, str], ...] = (
    ("journal", "journal entries"),
    ("notes", "notes"),
    ("chat", "chat messages"),
    ("polls", "polls"),
    ("reminders", "reminders"),
    ("relationships", "member relationships"),
    ("cross_system_relationships", "cross-system relationships"),
    ("external_contacts", "external contacts"),
    ("external_relationships", "external relationships"),
    ("sub_systems", "subsystems"),
    ("places", "places"),
    ("map_nodes", "map nodes"),
    ("privacy_buckets", "privacy buckets"),
    ("useful_links", "useful links"),
)

# Free-text member attributes BerryTree carries that Sheaf has no column for.
# They land as custom fields rather than being dropped: a text field named
# "Role" holding "the one who drives" is a faithful enough home, and the
# alternative is losing it. Only created when at least one member has a value.
_MEMBER_TEXT_ATTRS: tuple[tuple[str, str], ...] = (
    ("role", "Role"),
    ("mood", "Mood"),
)

# Keys a BerryTree custom-field entry might carry its name and value under.
# The sample's `custom_fields` arrays are all empty, so this is tolerant by
# necessity: anything that does not yield a name and a value is counted and
# reported rather than guessed at.
_FIELD_NAME_KEYS = ("name", "label", "title", "key")
_FIELD_VALUE_KEYS = ("value", "text", "content")
_FIELD_REF_KEYS = ("field_id", "template_id", "id")


def _collection(data: dict, name: str) -> list[dict]:
    """Return a BerryTree section as a list of dict rows.

    Missing, null, or non-list sections normalise to []; non-dict entries
    inside a list are dropped, so one malformed row can't crash a walk."""
    raw = data.get(name)
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def _section_len(data: dict, name: str) -> int:
    """Length of a section for reporting, counting every entry including
    malformed ones (`_collection` drops those, and a dropped record is still
    a record the user had)."""
    raw = data.get(name)
    return len(raw) if isinstance(raw, list) else 0


def _coerce_str(value: object) -> str | None:
    """Return a plain string, or None for null / non-string values.

    A non-string value is dropped, never str()-quoted, so member content
    can't leak into an error message or a plaintext job event."""
    return value if isinstance(value, str) else None


def _nonempty(value: object) -> str | None:
    """A trimmed non-empty string, or None. BerryTree writes "" for unset
    text rather than omitting the key, so almost every read wants this."""
    text = _coerce_str(value)
    if text is None:
        return None
    text = text.strip()
    return text or None


def _parse_bt_time(value: object) -> datetime | None:
    """Parse a BerryTree timestamp: ISO-8601, usually zone-qualified.

    Naive values are read as UTC (the exporter writes +00:00, but a
    hand-edited file may not). Anything unparseable returns None so one bad
    row can't abort the import."""
    text = _coerce_str(value)
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _normalize_color(color: object) -> str | None:
    """Normalize a colour to '#rrggbb', or None.

    BerryTree writes 6-hex with a leading '#'; 3-hex shorthand and 8-hex
    ARGB are accepted too so a hand-edited or older file still lands a
    usable colour instead of a mangled one."""
    if not isinstance(color, str):
        return None
    s = color.strip().lstrip("#")
    if len(s) == 3:
        s = f"{s[0] * 2}{s[1] * 2}{s[2] * 2}"
    elif len(s) == 8:
        # ARGB -> RGB: drop the leading 2 alpha chars (alpha is the high byte).
        s = s[2:]
    if len(s) != 6 or not all(c in "0123456789abcdefABCDEF" for c in s):
        return None
    return f"#{s.lower()}"


def _map_privacy(is_private: object) -> PrivacyLevel:
    """Map BerryTree's boolean privacy to our enum. Non-bool / missing ->
    private, so a malformed flag fails closed."""
    return PrivacyLevel.PUBLIC if is_private is False else PrivacyLevel.PRIVATE


def _avatar_url(value: object) -> str | None:
    """Resolve a BerryTree image reference to a policy-gated external URL.

    BerryTree's server has been down throughout this importer's development,
    so we have never seen a populated `avatar` on a real export - the sample
    carries "" with `has_avatar: false`. Whatever shape a populated one takes
    (a bare storage key, an app-relative path, an absolute URL), routing it
    through `sanitize_external_avatar_url` is the correct gate: an http(s)
    URL to a host that is not ours survives subject to the hotlink policy,
    and anything else is dropped rather than stored as a dangling reference.
    Members whose avatar could not be carried are counted and reported."""
    return sanitize_external_avatar_url(value)


def _is_template(member: dict) -> bool:
    return member.get("is_template") is True


def _member_tag_names(member: dict) -> list[str]:
    """Tag names off a member row.

    `tags` is empty in every sample we have, so both plausible shapes are
    accepted: a list of plain names, or a list of objects carrying a name."""
    raw = member.get("tags")
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            name = _nonempty(entry)
        elif isinstance(entry, dict):
            name = next(
                (_nonempty(entry.get(k)) for k in _FIELD_NAME_KEYS if _nonempty(entry.get(k))),
                None,
            )
        else:
            name = None
        if name:
            names.append(name)
    return names


def _field_template_names(data: dict) -> dict[str, str]:
    """Map custom-field template id -> field name, for member field entries
    that reference a template instead of naming the field inline."""
    names: dict[str, str] = {}
    for tpl in _collection(data, "field_templates"):
        tpl_id = _nonempty(tpl.get("id"))
        name = next(
            (_nonempty(tpl.get(k)) for k in _FIELD_NAME_KEYS if _nonempty(tpl.get(k))),
            None,
        )
        if tpl_id and name:
            names[tpl_id] = name
    return names


def _member_field_pairs(
    member: dict, template_names: dict[str, str]
) -> tuple[list[tuple[str, str]], int]:
    """(name, value) pairs off a member's `custom_fields`, plus a count of
    entries we could not make sense of.

    Handles a field entry that names itself inline and one that references a
    `field_templates` row by id. An entry yielding neither a name nor a value
    is counted into the second return value so the run can report "N custom
    field entries were in a shape this importer does not recognise" - loudly
    unhandled beats quietly dropped."""
    raw = member.get("custom_fields")
    if not isinstance(raw, list):
        return [], 0
    pairs: list[tuple[str, str]] = []
    unrecognised = 0
    for entry in raw:
        if not isinstance(entry, dict):
            unrecognised += 1
            continue
        name = next(
            (_nonempty(entry.get(k)) for k in _FIELD_NAME_KEYS if _nonempty(entry.get(k))),
            None,
        )
        if name is None:
            ref = next(
                (_nonempty(entry.get(k)) for k in _FIELD_REF_KEYS if _nonempty(entry.get(k))),
                None,
            )
            if ref is not None:
                name = template_names.get(ref)
        value = next(
            (_nonempty(entry.get(k)) for k in _FIELD_VALUE_KEYS if _nonempty(entry.get(k))),
            None,
        )
        if name is None or value is None:
            unrecognised += 1
            continue
        pairs.append((name, value))
    return pairs, unrecognised


def _member_folder_ids(member: dict) -> list[str]:
    """Folder ids a member belongs to, across both keys BerryTree writes
    (`folder_ids` plural, and the older singular `folder_id`)."""
    ids: list[str] = []
    raw = member.get("folder_ids")
    if isinstance(raw, list):
        ids.extend(fid for fid in (_nonempty(f) for f in raw) if fid)
    single = _nonempty(member.get("folder_id"))
    if single and single not in ids:
        ids.append(single)
    return ids


def _main_context(data: dict) -> dict:
    """The main `system_contexts` row - BerryTree keeps the system's own
    profile (name, description, avatar, colour, tag, pronouns) there rather
    than on the thin top-level `system` object."""
    contexts = _collection(data, "system_contexts")
    for ctx in contexts:
        if ctx.get("kind") == "main":
            return ctx
    return contexts[0] if contexts else {}


def _system_name(data: dict) -> str | None:
    """System display name: the main context's name, else the top-level
    `system_name`, else the account `username`."""
    system = data.get("system")
    system = system if isinstance(system, dict) else {}
    return (
        _nonempty(_main_context(data).get("name"))
        or _nonempty(system.get("system_name"))
        or _nonempty(system.get("username"))
    )


def _split_custom_statuses(data: dict) -> tuple[list[dict], list[dict]]:
    """Split `custom_statuses` into (fronting types, custom fronts).

    BerryTree keeps two different things in one array, discriminated by
    `kind`: "type" rows are fronting types ("Co-conscious", "Blurry") that
    annotate a front entry, and "status" rows are standalone fronting
    entities ("Asleep") that front on their own with no member attached -
    exactly Sheaf's custom fronts. An unrecognised `kind` is treated as a
    custom front, because surfacing it as a roster row the user can delete
    beats having it vanish."""
    types: list[dict] = []
    fronts: list[dict] = []
    for row in _collection(data, "custom_statuses"):
        if row.get("kind") == "type":
            types.append(row)
        else:
            fronts.append(row)
    return types, fronts


def _unsupported_sections(data: dict) -> list[BTUnsupportedSection]:
    """Non-empty sections this importer cannot map, in report order.

    Extra system contexts and layers are reported past the first of each:
    every export has a main context and a default layer, so those two carry
    no information, but a second one means the user has structure (BerryTree's
    multi-context / layered roster model) that Sheaf has no equivalent for."""
    out = [
        BTUnsupportedSection(name=label, count=_section_len(data, key))
        for key, label in _UNSUPPORTED_SECTIONS
        if _section_len(data, key) > 0
    ]
    extra_contexts = max(0, _section_len(data, "system_contexts") - 1)
    if extra_contexts:
        out.append(
            BTUnsupportedSection(name="extra system contexts", count=extra_contexts)
        )
    extra_layers = max(0, _section_len(data, "layers") - 1)
    if extra_layers:
        out.append(BTUnsupportedSection(name="layers", count=extra_layers))
    return out


def _unsupported_warning(sections: list[BTUnsupportedSection]) -> str | None:
    """One warning line covering everything the file carried that we left
    behind, with the invitation to send it to us.

    Names sections and counts only, never content: this string reaches the
    job's plaintext event log."""
    if not sections:
        return None
    listed = ", ".join(f"{s.count} {s.name}" for s in sections)
    return (
        f"Left behind: {listed}. BerryTree support is experimental - it maps "
        "members, custom fronts, fronting history and folders, because those "
        "are the only parts of the format we have been able to see a real "
        "example of. If you need any of the above, please get in touch and "
        "bring your export: that is what lets us add it."
    )


def _export_errors(data: dict) -> list[str]:
    """BerryTree's own record of sections its exporter could not write.

    Capped in both count and length: this is untrusted text sized by the
    uploader. Preview-only by design - see BTPreviewSummary.export_errors."""
    raw = data.get("_partial_errors")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw[:20]:
        text = _nonempty(entry)
        if text:
            out.append(text[:200])
    return out


def measure_berrytree_payload(data: dict, report: ClampReport) -> None:
    """Tally which BerryTree fields exceed the schema caps, into ``report``
    - the warn-before-import prediction.

    Walks the same keys ``run_import`` clamps, with the same caps, so the
    preview's warnings match what the import would shorten. Only string
    values are measured (guarded by ``_coerce_str``) so a malformed upload
    can't raise."""

    def s(value: object, cap: il.Cap) -> None:
        text = _coerce_str(value)
        if text is not None:
            clamp_str(text, cap, report=report)

    ctx = _main_context(data)
    s(_system_name(data), il.SYS_NAME)
    s(ctx.get("description"), il.SYS_DESCRIPTION)
    s(ctx.get("tag"), il.SYS_TAG)

    template_names = _field_template_names(data)
    for m in _collection(data, "members"):
        s(m.get("name"), il.M_NAME)
        s(m.get("display_name"), il.M_DISPLAY_NAME)
        s(m.get("pronouns"), il.M_PRONOUNS)
        s(m.get("description"), il.M_DESCRIPTION)
        s(m.get("emoji"), il.M_EMOJI)
        for tag_name in _member_tag_names(m):
            s(tag_name, il.TAG_NAME)
        for field_name, _value in _member_field_pairs(m, template_names)[0]:
            s(field_name, il.CF_NAME)
        for attr, label in _MEMBER_TEXT_ATTRS:
            if _nonempty(m.get(attr)) is not None:
                s(label, il.CF_NAME)

    _, custom_fronts = _split_custom_statuses(data)
    for cf in custom_fronts:
        s(cf.get("name"), il.M_NAME)
        s(cf.get("emoji"), il.M_EMOJI)

    for f in _collection(data, "folders"):
        s(f.get("name"), il.GROUP_NAME)
        s(f.get("description"), il.GROUP_DESCRIPTION)


def _distinct_tag_count(data: dict) -> int:
    names: set[str] = set()
    for m in _collection(data, "members"):
        names.update(_member_tag_names(m))
    return len(names)


def _distinct_field_count(data: dict) -> int:
    template_names = _field_template_names(data)
    names: set[str] = set()
    for m in _collection(data, "members"):
        names.update(name for name, _ in _member_field_pairs(m, template_names)[0])
        for attr, label in _MEMBER_TEXT_ATTRS:
            if _nonempty(m.get(attr)) is not None:
                names.add(label)
    return len(names)


def preview(data: dict) -> BTPreviewSummary:
    """Parse BerryTree export JSON and return a summary for the user."""
    members = _collection(data, "members")
    real_members = [m for m in members if not _is_template(m)]
    templates = [m for m in members if _is_template(m)]
    fronting_types, custom_fronts = _split_custom_statuses(data)

    raw_version = data.get("schema_version")
    schema_version = (
        raw_version
        if isinstance(raw_version, int) and not isinstance(raw_version, bool)
        else None
    )

    summary = BTPreviewSummary(
        system_name=_system_name(data),
        schema_version=schema_version,
        member_count=len(real_members),
        members=[
            BTPreviewMember(
                id=_nonempty(m.get("id")) or "",
                name=_nonempty(m.get("name")) or "unnamed",
            )
            for m in real_members
        ],
        template_count=len(templates),
        custom_front_count=len(custom_fronts),
        custom_fronts=[
            BTPreviewCustomFront(
                id=_nonempty(cf.get("id")) or "",
                name=_nonempty(cf.get("name")) or "unnamed",
            )
            for cf in custom_fronts
        ],
        fronting_type_count=len(fronting_types),
        front_history_count=_section_len(data, "front_entries"),
        folder_count=_section_len(data, "folders"),
        tag_count=_distinct_tag_count(data),
        custom_field_count=_distinct_field_count(data),
        unsupported_sections=_unsupported_sections(data),
        export_errors=_export_errors(data),
    )

    # Predict which over-cap fields the real import would shorten, so the
    # user sees the warning before committing. Same caps as run_import.
    report = ClampReport()
    measure_berrytree_payload(data, report)
    warnings = report.to_warnings() + il.import_row_cap_warnings(
        {
            "fronts": summary.front_history_count,
            "groups": summary.folder_count,
            "tags": summary.tag_count,
            "custom_fields": summary.custom_field_count,
        }
    )
    if schema_version is not None and schema_version != KNOWN_SCHEMA_VERSION:
        warnings.append(
            f"This export is schema version {schema_version}; BerryTree "
            f"support was built against version {KNOWN_SCHEMA_VERSION}. It "
            "will still import, but if anything looks wrong afterwards, "
            "that version number is the useful thing to tell us."
        )
    unsupported = _unsupported_warning(summary.unsupported_sections)
    if unsupported:
        warnings.append(unsupported)
    summary.limit_warnings = warnings
    return summary


async def run_import(
    data: dict,
    options: BTImportOptions,
    system: System,
    db: AsyncSession,
) -> BTImportResult:
    """Import BerryTree export data into the user's system."""
    result = BTImportResult()
    warnings: list[str] = []
    report = ClampReport()

    # --- System profile ---
    # BerryTree's top-level `system` object carries `username`, `system_name`
    # and `email`. The email is never read: it is the address of an account on
    # somebody else's service, it is not needed to place any of this data
    # (every row is scoped by the authenticated `system` argument), and
    # Sheaf's own email column is the user's login identity, which an
    # uploaded file has no business writing. The profile fields come from the
    # main system_context instead, which is where BerryTree actually keeps
    # them.
    ctx = _main_context(data)
    if options.system_profile:
        bt_name = _system_name(data)
        if bt_name and not system.name:
            system.name = clamp_str(bt_name, il.SYS_NAME, report=report)
        # Descriptions are markdown written in another app, so they go through
        # the internal-image strip before being stored: a ref to this
        # instance's storage would be re-signed into a live capability URL on
        # read, letting a crafted export read another account's upload through
        # the importing user's own profile. Clamp before the strip so the
        # length bound also caps the superlinear markdown image parse.
        bt_desc = strip_internal_image_refs_md_to_none(
            clamp_str(
                _nonempty(ctx.get("description")), il.SYS_DESCRIPTION, report=report
            )
        )
        if bt_desc:
            system.description = bt_desc
        system.color = _normalize_color(ctx.get("color")) or system.color
        bt_tag = clamp_str(_nonempty(ctx.get("tag")), il.SYS_TAG, report=report)
        if bt_tag:
            system.tag = bt_tag
        ctx_avatar = _avatar_url(ctx.get("avatar"))
        if ctx_avatar:
            system.avatar_url = ctx_avatar

    # --- Members ---
    bt_members = _collection(data, "members")
    if options.member_ids is not None:
        selected = set(options.member_ids)
        bt_members = [m for m in bt_members if m.get("id") in selected]

    if not options.templates:
        templates = [m for m in bt_members if _is_template(m)]
        if templates:
            result.templates_skipped = len(templates)
            warnings.append(
                f"Skipped {len(templates)} template member(s). Templates are "
                "scaffolding for creating members rather than members "
                "themselves, and they count against your member limit, so "
                "they are left out unless you ask for them."
            )
        bt_members = [m for m in bt_members if not _is_template(m)]

    template_names = _field_template_names(data)
    # Build member + custom-front candidates first (no DB writes), so the
    # member-cap check below counts only the rows this run would CREATE.
    member_candidates: list[tuple[Member, str]] = []
    avatars_dropped = 0
    for bt_m in bt_members:
        bt_id = _nonempty(bt_m.get("id")) or ""
        plaintext_name = clamp_str(
            _nonempty(bt_m.get("name")) or "unnamed", il.M_NAME, report=report
        )
        # Same reason as the system description above (strip + length cap).
        plaintext_description = strip_internal_image_refs_md_to_none(
            clamp_str(
                _nonempty(bt_m.get("description")), il.M_DESCRIPTION, report=report
            )
        )
        avatar = _avatar_url(bt_m.get("avatar"))
        if avatar is None and bt_m.get("has_avatar") is True:
            avatars_dropped += 1
        member_id = uuid.uuid4()
        member = Member(
            id=member_id,
            system_id=system.id,
            name=encrypt(plaintext_name, aad=member_name_aad(member_id)),
            name_hash=blind_index(plaintext_name),
            display_name=clamp_str(
                _nonempty(bt_m.get("display_name")),
                il.M_DISPLAY_NAME,
                report=report,
            ),
            description=(
                encrypt(plaintext_description, aad=member_description_aad(member_id))
                if plaintext_description is not None
                else None
            ),
            pronouns=clamp_str(
                _nonempty(bt_m.get("pronouns")), il.M_PRONOUNS, report=report
            ),
            avatar_url=avatar,
            banner_url=_avatar_url(bt_m.get("banner")),
            color=_normalize_color(bt_m.get("color")),
            emoji=clamp_str(_nonempty(bt_m.get("emoji")), il.M_EMOJI, report=report),
            privacy=_map_privacy(bt_m.get("is_private")),
            # BerryTree's `counts_toward_headcount: false` is the same idea as
            # a Sheaf custom front: a roster row that fronts but is not a
            # person to be counted.
            is_custom_front=bt_m.get("counts_toward_headcount") is False,
        )
        if member.is_custom_front:
            member.fronting_private = default_fronting_private(is_custom_front=True)
        created = _parse_bt_time(bt_m.get("created_at"))
        if created:
            member.created_at = created
        if bt_m.get("archived") is True:
            # BerryTree records the flag but not when it was set, so this is
            # the import time. Archive is a soft-hide, not a destructive
            # action, and the timestamp is only used for ordering.
            member.archived_at = datetime.now(UTC)
        member_candidates.append((member, bt_id))

    # --- Custom statuses -> custom fronts ---
    fronting_types, bt_custom_fronts = _split_custom_statuses(data)
    custom_front_candidates: list[tuple[Member, str]] = []
    if options.custom_fronts:
        for bt_cf in bt_custom_fronts:
            bt_id = _nonempty(bt_cf.get("id")) or ""
            plaintext_cf_name = clamp_str(
                _nonempty(bt_cf.get("name")) or "unnamed", il.M_NAME, report=report
            )
            plaintext_cf_description = strip_internal_image_refs_md_to_none(
                clamp_str(
                    _nonempty(bt_cf.get("description")),
                    il.M_DESCRIPTION,
                    report=report,
                )
            )
            member_id = uuid.uuid4()
            member = Member(
                id=member_id,
                system_id=system.id,
                name=encrypt(plaintext_cf_name, aad=member_name_aad(member_id)),
                name_hash=blind_index(plaintext_cf_name),
                description=(
                    encrypt(
                        plaintext_cf_description,
                        aad=member_description_aad(member_id),
                    )
                    if plaintext_cf_description is not None
                    else None
                ),
                color=_normalize_color(bt_cf.get("color")),
                emoji=clamp_str(
                    _nonempty(bt_cf.get("emoji")), il.M_EMOJI, report=report
                ),
                avatar_url=_avatar_url(bt_cf.get("image_url")),
                is_custom_front=True,
                # BerryTree has no per-status share guard to carry, so this
                # takes the safe default: guarded, because "Asleep" is a state
                # nobody published by publishing a roster.
                fronting_private=default_fronting_private(is_custom_front=True),
            )
            created = _parse_bt_time(bt_cf.get("created_at"))
            if created:
                member.created_at = created
            custom_front_candidates.append((member, bt_id))

    # Match against the existing roster, then hard-fail before writing
    # anything if the NEW rows would blow the tier cap.
    index = await load_member_match_index(db, system.id)
    new_count = count_new_members(
        [
            candidate_key(m)
            for m, _ in (*member_candidates, *custom_front_candidates)
        ],
        index=index,
        strategy=options.conflict_strategy,
    )
    await enforce_import_member_cap(db, system, new_count)

    # Per-import row caps (bomb protection). Gross counts: dedup/skip only
    # reduces the real write count.
    il.enforce_import_row_caps(
        {
            "fronts": (
                _section_len(data, "front_entries") if options.front_history else 0
            ),
            "groups": _section_len(data, "folders") if options.folders else 0,
            "tags": _distinct_tag_count(data) if options.tags else 0,
            "custom_fields": (
                _distinct_field_count(data) if options.custom_fields else 0
            ),
        }
    )

    bt_id_to_member: dict[str, Member] = {}
    for member, bt_id in member_candidates:
        resolution = await resolve_member(
            member,
            index=index,
            strategy=options.conflict_strategy,
            db=db,
            system=system,
        )
        if resolution.privacy_held_member_id:
            result.members_privacy_skipped += 1
            warnings.append(privacy_hold_warning(resolution.privacy_held_member_id))
        if resolution.disposition == "created":
            db.add(resolution.member)
            result.members_imported += 1
        elif resolution.disposition == "updated":
            result.members_updated += 1
        else:
            result.members_skipped += 1
        if bt_id:
            bt_id_to_member[bt_id] = resolution.member

    bt_id_to_custom_front: dict[str, Member] = {}
    for member, bt_id in custom_front_candidates:
        resolution = await resolve_member(
            member,
            index=index,
            strategy=options.conflict_strategy,
            db=db,
            system=system,
        )
        if resolution.privacy_held_member_id:
            result.members_privacy_skipped += 1
            warnings.append(privacy_hold_warning(resolution.privacy_held_member_id))
        if resolution.disposition == "created":
            db.add(resolution.member)
            result.custom_fronts_imported += 1
        elif resolution.disposition == "updated":
            result.members_updated += 1
        else:
            result.members_skipped += 1
        if bt_id:
            bt_id_to_custom_front[bt_id] = resolution.member

    await db.flush()

    if avatars_dropped:
        warnings.append(
            f"{avatars_dropped} member(s) had an avatar in BerryTree that "
            "could not be carried across: the export references it by a "
            "handle that only BerryTree's own server can resolve, and that "
            "server is not reachable. Re-upload those avatars here."
        )

    all_bt_to_member = {**bt_id_to_member, **bt_id_to_custom_front}

    dedupe = options.conflict_strategy != ImportConflictStrategy.CREATE

    # --- Tags ---
    if options.tags:
        tag_index = (
            await load_tag_index(db, system.id) if dedupe else ContentMatchIndex()
        )
        tag_guard = (
            await load_member_tag_guard(db, system.id) if dedupe else PairGuard()
        )
        # Build every tag first and flush once, then wire the edges: a flush
        # per tag would be one round trip per member-tag pair.
        tag_edges: list[tuple[Tag, Member]] = []
        for bt_m in bt_members:
            member = bt_id_to_member.get(_nonempty(bt_m.get("id")) or "")
            if member is None:
                continue
            for raw_name in _member_tag_names(bt_m):
                name = clamp_str(raw_name, il.TAG_NAME, report=report)
                tag = tag_index.get(name)
                if tag is None:
                    tag = Tag(id=uuid.uuid4(), system_id=system.id, name=name)
                    db.add(tag)
                    tag_index.register(name, tag)
                    result.tags_imported += 1
                else:
                    result.tags_skipped += 1
                tag_edges.append((tag, member))
        if tag_edges:
            await db.flush()
        for tag, member in tag_edges:
            if tag_guard.add((tag.id, member.id)):
                await db.execute(
                    member_tags.insert().values(tag_id=tag.id, member_id=member.id)
                )

    # --- Custom fields ---
    if options.custom_fields:
        # Definitions dedupe by (name, type) unconditionally, matching every
        # other importer: a re-import must not litter the field list with a
        # second copy of every column.
        field_index = await load_field_def_index(db, system.id)
        value_guard = await load_field_value_guard(db, system.id)
        unrecognised_fields = 0
        oversized_values = 0
        next_order = 0
        # Definitions first, values second, one flush between: the values
        # carry an FK to the definition, so the definitions have to exist,
        # but only once for the whole run rather than once per value.
        pending_values: list[tuple[CustomFieldDefinition, Member, str]] = []
        for bt_m in bt_members:
            member = bt_id_to_member.get(_nonempty(bt_m.get("id")) or "")
            if member is None:
                continue
            pairs, unrecognised = _member_field_pairs(bt_m, template_names)
            unrecognised_fields += unrecognised
            for attr, label in _MEMBER_TEXT_ATTRS:
                value = _nonempty(bt_m.get(attr))
                if value is not None:
                    pairs.append((label, value))
            for raw_name, raw_value in pairs:
                name = clamp_str(raw_name, il.CF_NAME, report=report)
                key = (name, FieldType.TEXT.value)
                field_def = field_index.get(key)
                if field_def is None:
                    field_def = CustomFieldDefinition(
                        id=uuid.uuid4(),
                        system_id=system.id,
                        name=name,
                        field_type=FieldType.TEXT,
                        order=next_order,
                    )
                    next_order += 1
                    db.add(field_def)
                    field_index.register(key, field_def)
                    result.custom_fields_imported += 1
                else:
                    result.custom_fields_skipped += 1
                pending_values.append((field_def, member, raw_value))

        if pending_values:
            await db.flush()
        for field_def, member, raw_value in pending_values:
            # The (field, member) pair guard is unconditional: a reused
            # definition plus a deduped member would otherwise trip the
            # UNIQUE(field_id, member_id) constraint on every re-import.
            if not value_guard.add((field_def.id, member.id)):
                continue
            # Stored at full length; the editor's cap does not
            # retroactively edit an import. Counted for the report.
            if value_over_text_cap(raw_value):
                oversized_values += 1
            cfv_id = uuid.uuid4()
            db.add(
                CustomFieldValue(
                    id=cfv_id,
                    field_id=field_def.id,
                    member_id=member.id,
                    value=encrypt_field_value({"v": raw_value}, cfv_id),
                )
            )
        if oversized_values:
            warnings.append(il.oversized_field_values_warning(oversized_values))
        if unrecognised_fields:
            warnings.append(
                f"Skipped {unrecognised_fields} custom field entr"
                f"{'y' if unrecognised_fields == 1 else 'ies'} written in a "
                "shape this importer does not recognise. BerryTree support is "
                "experimental and this is one of the parts we have never seen "
                "real data for - please get in touch with your export so we "
                "can read it properly."
            )

    # --- Folders -> groups ---
    if options.folders:
        bt_folders = _collection(data, "folders")
        bt_fid_to_group: dict[str, Group] = {}
        group_index = (
            await load_group_index(db, system.id) if dedupe else ContentMatchIndex()
        )
        created_group_ids: set[uuid.UUID] = set()

        # First pass: create groups without parent links.
        for bt_f in bt_folders:
            bt_fid = _nonempty(bt_f.get("id")) or ""
            name = clamp_str(
                _nonempty(bt_f.get("name")) or "unnamed", il.GROUP_NAME, report=report
            )
            existing = group_index.get(name) if dedupe else None
            if existing is not None:
                if bt_fid:
                    bt_fid_to_group[bt_fid] = existing
                result.groups_skipped += 1
                continue
            group = Group(
                id=uuid.uuid4(),
                system_id=system.id,
                name=name,
                # Same internal-image strip and length cap as the other
                # markdown fields above.
                description=strip_internal_image_refs_md_to_none(
                    clamp_str(
                        _nonempty(bt_f.get("description")),
                        il.GROUP_DESCRIPTION,
                        report=report,
                    )
                ),
                color=_normalize_color(bt_f.get("color")),
                privacy=_map_privacy(bt_f.get("is_private")),
            )
            db.add(group)
            group_index.register(name, group)
            created_group_ids.add(group.id)
            if bt_fid:
                bt_fid_to_group[bt_fid] = group
            result.groups_imported += 1

        await db.flush()

        # Second pass: parent links, then membership from the member rows
        # (BerryTree records the edge on the member, not the folder).
        unresolvable_parents = 0
        for bt_f in bt_folders:
            group = bt_fid_to_group.get(_nonempty(bt_f.get("id")) or "")
            if group is None or group.id not in created_group_ids:
                continue
            bt_parent = _nonempty(bt_f.get("parent_id"))
            if not bt_parent:
                continue
            parent_group = bt_fid_to_group.get(bt_parent)
            if parent_group is not None:
                group.parent_id = parent_group.id
            else:
                unresolvable_parents += 1
        if unresolvable_parents:
            warnings.append(
                f"Dropped {unresolvable_parents} folder parent link(s) that "
                "pointed at a folder not present in the export."
            )

        group_member_guard = (
            await load_group_member_guard(db, system.id) if dedupe else PairGuard()
        )
        unknown_folder_refs = 0
        for bt_m in bt_members:
            member = bt_id_to_member.get(_nonempty(bt_m.get("id")) or "")
            if member is None:
                continue
            for bt_fid in _member_folder_ids(bt_m):
                group = bt_fid_to_group.get(bt_fid)
                if group is None:
                    unknown_folder_refs += 1
                    continue
                if not group_member_guard.add((group.id, member.id)):
                    continue
                await db.execute(
                    group_members.insert().values(
                        group_id=group.id, member_id=member.id
                    )
                )
        if unknown_folder_refs:
            warnings.append(
                f"Dropped {unknown_folder_refs} folder membership reference(s) "
                "whose folder wasn't in the export."
            )

        # Clamp nesting depth. BerryTree builds a folder tree with no depth
        # check, so a deep (or looping) export could exceed the API's
        # MAX_GROUP_DEPTH. Mirror the other importers: reparent anything we
        # created that ended too deep, or whose parent chain loops.
        if created_group_ids:
            from sheaf.api.v1.groups import MAX_GROUP_DEPTH
            from sheaf.services.sheaf_import import correct_nesting_depth

            await db.flush()
            depth_rows = await db.execute(
                select(Group.id, Group.parent_id).where(Group.system_id == system.id)
            )
            parent_of = {gid: pid for gid, pid in depth_rows.all()}
            correction = correct_nesting_depth(parent_of, max_depth=MAX_GROUP_DEPTH)
            depth_moved = 0
            cycle_moved = 0
            for grp in bt_fid_to_group.values():
                if grp.id not in created_group_ids:
                    continue
                if grp.id in correction.moved:
                    grp.parent_id = correction.parent_of[grp.id]
                    depth_moved += 1
                elif grp.id in correction.cycle_broken:
                    grp.parent_id = correction.parent_of[grp.id]
                    cycle_moved += 1
            if depth_moved:
                warnings.append(
                    f"{depth_moved} folder(s) exceed the maximum nesting depth "
                    f"({MAX_GROUP_DEPTH}) and were moved up to fit."
                )
            if cycle_moved:
                warnings.append(
                    f"{cycle_moved} folder(s) had a looping parent reference "
                    "and were moved to the top level."
                )

    # --- Front history ---
    if options.front_history:
        # Fronting types annotate a front rather than being one, and Sheaf has
        # no column for them, so the type's name rides along in the front's
        # free-text status where it stays visible instead of being dropped.
        type_names: dict[str, str] = {}
        for bt_type in fronting_types:
            type_id = _nonempty(bt_type.get("id"))
            type_name = _nonempty(bt_type.get("name"))
            if type_id and type_name:
                type_names[type_id] = type_name

        front_index = (
            await load_front_index(db, system.id) if dedupe else ContentMatchIndex()
        )
        missing_member = 0
        missing_ref = 0
        bad_timestamp = 0
        swapped_count = 0
        front_edges: list[tuple[uuid.UUID, uuid.UUID]] = []
        for bt_f in _collection(data, "front_entries"):
            # A front entry names EITHER a member or a custom status. Both id
            # fields are looked up in both maps so a status mis-filed by its
            # `kind` still resolves.
            ref = _nonempty(bt_f.get("member_id")) or _nonempty(
                bt_f.get("custom_status_id")
            )
            if not ref:
                missing_ref += 1
                continue
            member = all_bt_to_member.get(ref)
            if member is None:
                missing_member += 1
                continue

            started = _parse_bt_time(bt_f.get("started_at"))
            if not started:
                bad_timestamp += 1
                continue
            ended = _parse_bt_time(bt_f.get("ended_at"))
            started, ended, swapped = normalize_front_interval(started, ended)
            if swapped:
                swapped_count += 1

            if dedupe:
                fkey = front_key(started, ended, {member.id})
                if front_index.get(fkey) is not None:
                    result.fronts_skipped += 1
                    continue
                front_index.register(fkey)

            parts = [
                type_names.get(_nonempty(bt_f.get("fronting_type_id")) or ""),
                _nonempty(bt_f.get("custom_status")),
                _nonempty(bt_f.get("note")),
            ]
            status_text = " - ".join(p for p in parts if p) or None

            front_id = uuid.uuid4()
            front = Front(
                id=front_id,
                system_id=system.id,
                started_at=started,
                ended_at=ended,
                custom_status=(
                    encrypt(status_text, aad=front_custom_status_aad(front_id))
                    if status_text
                    else None
                ),
            )
            db.add(front)
            front_edges.append((front_id, member.id))
            result.fronts_imported += 1

        # One flush for the whole history, then the member links. A front
        # history is the largest section in a real export by an order of
        # magnitude, so a flush per row is the difference between an import
        # that finishes and one that times out.
        if front_edges:
            await db.flush()
            await db.execute(
                front_members.insert(),
                [
                    {"front_id": front_id, "member_id": member_id}
                    for front_id, member_id in front_edges
                ],
            )

        if missing_member:
            warnings.append(
                f"Skipped {missing_member} front entr"
                f"{'y' if missing_member == 1 else 'ies'} that referenced a "
                "member or status not selected for import."
            )
        if missing_ref:
            warnings.append(
                f"Skipped {missing_ref} front entr"
                f"{'y' if missing_ref == 1 else 'ies'} naming neither a member "
                "nor a status (malformed export row)."
            )
        if bad_timestamp:
            warnings.append(
                f"Skipped {bad_timestamp} front entr"
                f"{'y' if bad_timestamp == 1 else 'ies'} with a missing or "
                "unreadable start time."
            )
        if swapped_count:
            warnings.append(
                f"Adjusted {swapped_count} front "
                f"{'entry' if swapped_count == 1 else 'entries'} whose end "
                "time was before the start time (swapped the two)."
            )

    # --- Everything we did not map ---
    unsupported = _unsupported_warning(_unsupported_sections(data))
    if unsupported:
        warnings.append(unsupported)

    result.warnings = warnings + report.to_warnings()
    return result
