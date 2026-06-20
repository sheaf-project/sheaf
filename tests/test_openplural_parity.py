"""OpenPlural export-coverage guard.

``test_export_import_parity.py`` proves every user-data column is either
*exported* in the native Article-20 dump or deliberately *excluded*. This
test extends that to the OpenPlural exporter: every column the native
export carries (each model's ``exported`` set) must have a stated
disposition in the OpenPlural envelope, one of:

* ``core``  - mapped to an OpenPlural v0.1 core record/field,
* ``ext``   - preserved under ``extensions.sheaf.*`` (lossless, opaque),
* ``gap``   - intentionally not carried, with a reason.

The dispositions below are read against ``CLASSIFICATION`` live, so a
newly-added *exported* column fails here until someone decides how the
OpenPlural exporter should treat it (and wires it into
``openplural_export.build_envelope``). That is the whole point: it stops
a new field silently falling out of the OpenPlural format the same way
the native parity guard stops it falling out of the native one.
"""

from __future__ import annotations

import pytest

from tests.test_export_import_parity import CLASSIFICATION

# Per model: every column in that model's `exported` set maps to exactly
# one disposition. `gap` carries a reason string; `core`/`ext`/`residual`
# are bare markers. Keep in sync with openplural_export.build_envelope.
CORE = "core"
EXT = "ext"
# The preservation channel itself: it does not map to one envelope field,
# it carries the FOREIGN residual (other apps' extensions/modules) that is
# re-merged into the envelope on export. See openplural_archive.py.
RESIDUAL = "residual"

DISPOSITION: dict[str, dict[str, object]] = {
    "System": {
        # Core OpenPlural System fields.
        "name": CORE, "description": CORE, "tag": CORE, "color": CORE,
        "privacy": CORE, "avatar_url": CORE,
        # extensions.sheaf.* (note + prefs + the safety/retention blocks).
        "note": EXT, "date_format": EXT, "replace_fronts_default": EXT,
        "coalesce_contiguous_fronts": EXT, "delete_confirmation": EXT,
        "auto_pin_first_revision": EXT,
        "safety_grace_period_days": EXT, "safety_applies_to_members": EXT,
        "safety_applies_to_groups": EXT, "safety_applies_to_tags": EXT,
        "safety_applies_to_fields": EXT, "safety_applies_to_fronts": EXT,
        "safety_applies_to_journals": EXT, "safety_applies_to_images": EXT,
        "safety_applies_to_revisions": EXT,
        "safety_applies_to_notifications": EXT,
        "safety_applies_to_reminders": EXT, "safety_applies_to_polls": EXT,
        "safety_applies_to_messages": EXT,
        "journal_max_revisions": EXT, "journal_max_revision_days": EXT,
        "pinned_revision_max_per_target": EXT,
        "openplural_archive": RESIDUAL,
    },
    "Member": {
        "name": CORE, "display_name": CORE, "description": CORE,
        "pronouns": CORE, "avatar_url": CORE, "banner_url": CORE,
        "color": CORE, "birthday": CORE, "is_custom_front": CORE,
        "privacy": CORE, "created_at": CORE,
        # pluralkit_id becomes a SourceRef(app="pluralkit").
        "pluralkit_id": CORE,
        # extensions.sheaf.* on the member record.
        "emoji": EXT, "note": EXT, "quick_switch_pin": EXT,
        "notify_on_front_global": EXT, "notify_on_front_self": EXT,
        "notify_on_front_member_ids": EXT,
    },
    "Front": {
        "started_at": CORE, "ended_at": CORE, "custom_status": CORE,
    },
    "Group": {
        "name": CORE, "description": CORE, "color": CORE, "parent_id": CORE,
    },
    "Tag": {
        "name": CORE, "color": CORE,
    },
    "CustomFieldDefinition": {
        "name": CORE, "field_type": CORE, "options": CORE, "order": CORE,
        "privacy": CORE,
    },
    "CustomFieldValue": {
        "member_id": CORE, "value": CORE,
    },
    "JournalEntry": {
        "title": CORE, "body": CORE, "visibility": CORE,
        "author_member_ids": CORE, "image_keys": CORE,
        "created_at": CORE, "updated_at": CORE,
        "member_id": EXT, "author_member_names": EXT,
        "author_user_id": (
            "GAP: journal author account id is re-pointed to the importing "
            "user by the native importer, so it is not portable content"
        ),
    },
    "Message": {
        # Mapped to boards.posts[] ...
        "board_member_id": CORE, "author_member_id": CORE, "body": CORE,
        "created_at": CORE, "updated_at": CORE,
        # ... with these in the post's extensions.sheaf.
        "board_kind": EXT, "parent_message_id": EXT,
    },
    # Everything below is carried verbatim under a file-level
    # extensions.sheaf.<section> passthrough (polls / reminders /
    # revisions / watch_tokens / uploaded_files), so every exported
    # column is `ext`.
    "ContentRevision": "_all_ext",
    "WatchToken": "_all_ext",
    "NotificationChannel": "_all_ext",
    "NotificationChannelGroupRule": "_all_ext",
    "NotificationChannelMemberRule": "_all_ext",
    "UploadedFile": "_all_ext",
    "Reminder": "_all_ext",
    "Poll": "_all_ext",
    "PollOption": "_all_ext",
    "PollVote": "_all_ext",
    "PollVoteEvent": "_all_ext",
}


def _disposition_for(model_name: str, exported: set[str]) -> dict[str, object]:
    entry = DISPOSITION.get(model_name)
    if entry == "_all_ext":
        return {col: EXT for col in exported}
    assert isinstance(entry, dict), (
        f"{model_name} has no OpenPlural disposition. Add it to "
        f"tests/test_openplural_parity.py (core / ext / gap per column)."
    )
    return entry


@pytest.mark.parametrize(
    "model", list(CLASSIFICATION), ids=lambda m: m.__name__
)
def test_every_exported_column_has_an_openplural_disposition(model: type):
    """Every natively-exported column must have an OpenPlural disposition.

    A new column added to a model's `exported` set in the native parity
    guard fails here until it is given a disposition and (if core/ext)
    threaded into openplural_export.build_envelope.
    """
    name = model.__name__
    exported: set[str] = set(CLASSIFICATION[model]["exported"])
    disposition = _disposition_for(name, exported)

    missing = exported - set(disposition)
    assert not missing, (
        f"{name}: exported column(s) {sorted(missing)} have no OpenPlural "
        f"disposition. Decide core / ext / gap in "
        f"tests/test_openplural_parity.py and wire core/ext fields into "
        f"openplural_export.build_envelope."
    )

    phantom = set(disposition) - exported
    assert not phantom, (
        f"{name}: disposition names column(s) {sorted(phantom)} that are no "
        f"longer in the native `exported` set. Remove them."
    )

    for col, disp in disposition.items():
        if disp in (CORE, EXT, RESIDUAL):
            continue
        assert isinstance(disp, str) and disp.startswith("GAP:"), (
            f"{name}.{col}: disposition must be 'core', 'ext', or a "
            f"'GAP: <reason>' string (got {disp!r})"
        )


def test_disposition_has_no_unknown_models():
    """Every model named in DISPOSITION is a real classified model - a
    rename in the native guard must not leave a stale entry here."""
    classified = {m.__name__ for m in CLASSIFICATION}
    stale = set(DISPOSITION) - classified
    assert not stale, (
        f"DISPOSITION names model(s) {sorted(stale)} not in the native "
        f"CLASSIFICATION. Renamed or dropped? Update this file."
    )
