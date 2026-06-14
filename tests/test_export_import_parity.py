"""Export / import field-parity guard.

Why this exists
---------------
The Article 20 export (``sheaf/api/v1/export.py``) and the native re-importer
(``sheaf/services/sheaf_import.py``) each hand-maintain a per-model field list.
When a user-data column is added to a model, it has to be threaded into BOTH or
the data silently fails to round-trip - exactly the failure the CLAUDE.md "add
new user-data fields to the export and import" rule exists to prevent, and
exactly the failure a prose rule keeps not preventing.

This test makes it mechanical. For every user-data model, each ORM column must
be classified as either:

* ``exported`` - its data rides along in the Article 20 dump and is consumed on
  re-import, or
* ``excluded`` - deliberately omitted, with a stated reason.

The column list is read live from the ORM (``Model.__table__.columns``), so a
newly-added column that nobody classified fails this test until someone decides
which bucket it belongs in. If it's user data, that decision means "go add it to
export.py and sheaf_import.py too".

What this test is NOT
---------------------
It does not assert the export *code* actually emits each ``exported`` column -
``test_account_export_completeness.py`` covers the behavioural half. This is the
structural guard: "did you make a decision about this new field, and is it the
right one?"

Any column flagged ``POSSIBLE GAP`` in its exclusion reason is a user-looking
field that is currently NOT exported: parked in ``excluded`` so this guard is
green, but a real product decision a maintainer should confirm (export it, or
confirm it is intentionally instance-local). Grep this file for ``POSSIBLE GAP``
(``test_possible_export_gaps_are_surfaced`` pins the current inventory).
"""

from __future__ import annotations

import pytest

from sheaf.models.content_revision import ContentRevision
from sheaf.models.custom_field import CustomFieldDefinition, CustomFieldValue
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.journal_entry import JournalEntry
from sheaf.models.member import Member
from sheaf.models.message import Message
from sheaf.models.notification_channel import NotificationChannel
from sheaf.models.notification_channel_group_rule import NotificationChannelGroupRule
from sheaf.models.notification_channel_member_rule import NotificationChannelMemberRule
from sheaf.models.poll import Poll, PollOption, PollVote, PollVoteEvent
from sheaf.models.reminder import Reminder
from sheaf.models.system import System
from sheaf.models.tag import Tag
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.watch_token import WatchToken

# Reusable exclusion reasons for the structural columns every model carries.
_SURROGATE_PK = "surrogate UUID PK, re-minted on import (old->new id maps handle refs)"
_TENANT_FK = "tenant scope FK, set from the importing system, not from file data"
_ROW_CREATED = "row-creation timestamp, server state not portable content"
_ROW_UPDATED = "row-mutation timestamp, server state not portable content"


# ---------------------------------------------------------------------------
# Classification. Per model: every ORM column must appear in exactly one of
# `exported` (set) or `excluded` (col -> reason). Keep these in sync with
# export.py / sheaf_import.py - that's the whole point.
# ---------------------------------------------------------------------------

CLASSIFICATION: dict[type, dict] = {
    System: {
        "exported": {
            "name", "description", "note", "tag", "avatar_url", "color",
            "privacy", "delete_confirmation", "date_format",
            "replace_fronts_default", "coalesce_contiguous_fronts",
            "auto_pin_first_revision", "safety_grace_period_days",
            "safety_applies_to_members", "safety_applies_to_groups",
            "safety_applies_to_tags", "safety_applies_to_fields",
            "safety_applies_to_fronts", "safety_applies_to_journals",
            "safety_applies_to_images", "safety_applies_to_revisions",
            "safety_applies_to_notifications", "safety_applies_to_reminders",
            "safety_applies_to_polls", "safety_applies_to_messages",
            "journal_max_revisions", "journal_max_revision_days",
            "pinned_revision_max_per_target",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "user_id": "owning account FK, re-pointed to the importing user",
            "created_at": _ROW_CREATED,
            "updated_at": _ROW_UPDATED,
        },
    },
    Member: {
        "exported": {
            "name", "display_name", "description", "pronouns", "avatar_url",
            "banner_url",
            "color", "birthday", "pluralkit_id", "emoji", "is_custom_front",
            "privacy", "note", "quick_switch_pin", "created_at",
            "notify_on_front_global", "notify_on_front_self",
            "notify_on_front_member_ids",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "name_hash": "derived blind index of name, recomputed on import",
            "updated_at": _ROW_UPDATED,
        },
    },
    Front: {
        "exported": {"started_at", "ended_at", "custom_status"},
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            # member_ids ride the front_members association, exported as a list.
        },
    },
    Group: {
        "exported": {"name", "description", "color", "parent_id"},
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "created_at": _ROW_CREATED,
            "updated_at": _ROW_UPDATED,
        },
    },
    Tag: {
        "exported": {"name", "color"},
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "created_at": _ROW_CREATED,
            "updated_at": _ROW_UPDATED,
        },
    },
    CustomFieldDefinition: {
        "exported": {"name", "field_type", "options", "order", "privacy"},
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "created_at": _ROW_CREATED,
            "updated_at": _ROW_UPDATED,
        },
    },
    CustomFieldValue: {
        "exported": {"member_id", "value"},
        "excluded": {
            "id": _SURROGATE_PK,
            "field_id": "parent definition FK, implied by export nesting",
        },
    },
    JournalEntry: {
        "exported": {
            "member_id", "title", "body", "visibility", "author_user_id",
            "author_member_ids", "author_member_names", "image_keys",
            "created_at", "updated_at",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
        },
    },
    ContentRevision: {
        "exported": {
            "target_type", "target_id", "user_id", "editor_member_ids",
            "editor_member_names", "title", "body", "image_keys",
            "created_at", "pinned_at",
        },
        "excluded": {
            "id": _SURROGATE_PK,
        },
    },
    WatchToken: {
        "exported": {"label", "revoked_at", "created_at"},
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "updated_at": _ROW_UPDATED,
        },
    },
    NotificationChannel: {
        "exported": {
            "watch_token_id", "name", "destination_type", "destination_config",
            "event_type", "base_all_members", "base_include_private",
            "trigger_on_start", "trigger_on_stop", "trigger_on_cofront_change",
            "cofront_redaction", "payload_sensitivity", "debounce_seconds",
            "aggregation_window_seconds", "quiet_hours", "created_at",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "updated_at": _ROW_UPDATED,
            # Per-instance recipient/runtime state and secrets - see the
            # _channel_dict docstring in export.py for the rationale.
            "destination_state": "recipient registration state, instance-local",
            "paused_by_sender": "runtime pause flag, instance-local",
            "activation_code_hash": "recipient activation secret, instance-local",
            "activation_code_expires_at": "activation expiry, instance-local",
            "redeemed_at": "recipient redemption state, instance-local",
            "redeemed_by_account_id": "recipient account FK, instance-local",
            "recipient_management_token_hash": "recipient secret, instance-local",
            "webhook_secret_encrypted": "webhook signing secret, re-entered by owner",
            "last_delivered_at": "delivery bookkeeping, runtime state",
            "email_monthly_used": "email quota counter, runtime state",
            "email_month_anchor": "email quota window anchor, runtime state",
            "email_delivery_mode": (
                "reserved for the unshipped email-delivery branch; NULL in v1. "
                "Export it once that feature lands and the column holds real "
                "owner config."
            ),
            "email_monthly_cap": (
                "reserved for the unshipped email-delivery branch; NULL in v1. "
                "Export it once that feature lands and the column holds real "
                "owner config."
            ),
        },
    },
    NotificationChannelGroupRule: {
        "exported": {"group_id", "rule", "include_private"},
        "excluded": {
            "channel_id": "parent channel FK, implied by export nesting",
        },
    },
    NotificationChannelMemberRule: {
        "exported": {"member_id", "rule"},
        "excluded": {
            "channel_id": "parent channel FK, implied by export nesting",
        },
    },
    UploadedFile: {
        "exported": {"key", "size_bytes", "content_type", "created_at"},
        "excluded": {
            "id": _SURROGATE_PK,
            "user_id": _TENANT_FK,
            "purpose": (
                "POSSIBLE GAP: file purpose tag, NOT in the export inventory. "
                "Low stakes (bytes don't round-trip via sync export anyway), "
                "but confirm."
            ),
        },
    },
    Reminder: {
        "exported": {
            "channel_id", "name", "title", "body", "enabled", "trigger_type",
            "trigger_member_id", "trigger_event", "delay_seconds",
            "schedule_kind", "schedule_time", "schedule_dow_mask",
            "schedule_dom", "schedule_tz", "cron_expression", "scope",
            "digest_when_absent", "created_at",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "last_fired_at": "delivery bookkeeping, runtime state",
            "updated_at": _ROW_UPDATED,
        },
    },
    Poll: {
        "exported": {
            "question", "description", "kind", "results_visibility",
            "closes_at", "retention_days", "include_custom_fronts",
            "restrict_voting_to_fronters", "created_at",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "updated_at": _ROW_UPDATED,
        },
    },
    PollOption: {
        "exported": {"text", "position"},
        "excluded": {
            "id": _SURROGATE_PK,
            "poll_id": "parent poll FK, implied by export nesting",
        },
    },
    PollVote: {
        "exported": {"voted_as_member_id", "option_ids", "created_at", "updated_at"},
        "excluded": {
            "id": _SURROGATE_PK,
            "poll_id": "parent poll FK, implied by export nesting",
        },
    },
    PollVoteEvent: {
        "exported": {
            "voted_as_member_id", "action", "option_ids",
            "fronting_member_ids", "created_at",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "poll_id": "parent poll FK, implied by export nesting",
            "actor_user_id": "acting account FK, meaningless on target instance",
        },
    },
    Message: {
        "exported": {
            "board_kind", "board_member_id", "author_member_id",
            "parent_message_id", "body", "created_at", "updated_at",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "deleted_at": "soft-delete tombstone; deleted rows are not exported",
        },
    },
}


def _columns(model: type) -> set[str]:
    """Actual DB column names for a model (relationships excluded)."""
    return set(model.__table__.columns.keys())


@pytest.mark.parametrize(
    "model", list(CLASSIFICATION), ids=lambda m: m.__name__
)
def test_every_user_data_column_is_classified(model: type):
    """Every ORM column on a user-data model must be classified exported or
    excluded. A new, unclassified column fails here - go decide which it is,
    and if it's user data, thread it into export.py AND sheaf_import.py."""
    entry = CLASSIFICATION[model]
    exported: set[str] = set(entry["exported"])
    excluded: dict[str, str] = entry["excluded"]

    overlap = exported & set(excluded)
    assert not overlap, (
        f"{model.__name__}: columns classified BOTH exported and excluded: "
        f"{sorted(overlap)}"
    )

    classified = exported | set(excluded)
    actual = _columns(model)

    unclassified = actual - classified
    assert not unclassified, (
        f"{model.__name__}: unclassified column(s) {sorted(unclassified)}.\n"
        f"Add each to the 'exported' set (and to export.py + sheaf_import.py if "
        f"it's user data) or to 'excluded' with a reason, in "
        f"tests/test_export_import_parity.py."
    )

    phantom = classified - actual
    assert not phantom, (
        f"{model.__name__}: classification names column(s) that no longer "
        f"exist on the model: {sorted(phantom)}. Remove them from the "
        f"classification (renamed? dropped?)."
    )


def test_every_exclusion_has_a_reason():
    """An excluded column without a stated reason is just a silent omission."""
    for model, entry in CLASSIFICATION.items():
        for col, reason in entry["excluded"].items():
            assert reason and reason.strip(), (
                f"{model.__name__}.{col} is excluded with no reason"
            )


def test_possible_export_gaps_are_surfaced():
    """Inventory the columns flagged POSSIBLE GAP so they stay visible until a
    maintainer resolves them. Update this expected set when one is exported or
    confirmed intentional - the change forces a conscious decision.
    """
    flagged: set[str] = set()
    for model, entry in CLASSIFICATION.items():
        for col, reason in entry["excluded"].items():
            if "POSSIBLE GAP" in reason:
                flagged.add(f"{model.__name__}.{col}")

    expected = {
        "UploadedFile.purpose",
    }
    assert flagged == expected, (
        "The POSSIBLE GAP set changed. If you resolved one (exported it or "
        "confirmed it's intentionally instance-local), update both its "
        "exclusion reason and this expected set."
    )
