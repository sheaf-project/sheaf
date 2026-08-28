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
from sheaf.models.relationship import (
    GroupRelationship,
    MemberRelationship,
    RelationshipType,
)
from sheaf.models.reminder import Reminder
from sheaf.models.share import (
    ShareGrant,
    ShareView,
    ShareViewField,
    ShareViewGroup,
    ShareViewMember,
)
from sheaf.models.system import System
from sheaf.models.tag import Tag
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import User
from sheaf.models.watch_token import WatchToken

# Reusable exclusion reasons for the structural columns every model carries.
_SURROGATE_PK = "surrogate UUID PK, re-minted on import (old->new id maps handle refs)"
_TENANT_FK = "tenant scope FK, set from the importing system, not from file data"
_ROW_CREATED = "row-creation timestamp, server state not portable content"
_ROW_UPDATED = "row-mutation timestamp, server state not portable content"
# Share rows land active on import because no grant is ever imported, so an
# imported view is exposed to nobody until the user deliberately publishes it.
_SHARE_LIFECYCLE = (
    "grace-window lifecycle state; imported rows land active because grants "
    "are never imported, so an imported view exposes nothing"
)
# A staged flag flip is mid-grace-window state, not curation: the export
# carries the view's LIVE flags, so an import never restores a half-applied
# loosening (and, with no grants imported, could not act on one anyway).
_SHARE_FLAG_STAGING = (
    "staged flag flip waiting out the grace window; the export carries the "
    "view's live flags, so an import never restores a half-applied loosening"
)
# A staged edge raise is the same kind of mid-grace-window state as a staged
# view flag: the export carries the edge's LIVE visibility, so an import never
# restores a half-applied raise (and, with no grants imported, could not act on
# one anyway).
_EDGE_RAISE_STAGING = (
    "staged privacy raise waiting out the grace window; the export carries the "
    "edge's live visibility, so an import never restores a half-applied raise"
)
# Same kind of mid-grace-window state as a staged edge raise, for the same
# reason: the export carries the group's LIVE privacy, so an import never
# restores a half-applied publish.
_GROUP_RAISE_STAGING = (
    "staged privacy raise waiting out the grace window; the export carries the "
    "group's live privacy, so an import never restores a half-applied raise"
)
# And once more for a custom-field definition, for the same reason: the export
# carries the definition's LIVE privacy, so an import never restores a
# half-applied raise.
_FIELD_RAISE_STAGING = (
    "staged privacy raise waiting out the grace window; the export carries the "
    "definition's live privacy, so an import never restores a half-applied raise"
)
# And for the system-level master switch, same reason: the export carries the
# system's LIVE privacy, so an import never restores a half-applied raise.
_SYSTEM_RAISE_STAGING = (
    "staged privacy raise waiting out the grace window; the export carries the "
    "system's live privacy, so an import never restores a half-applied raise"
)
_NO_GRANT_IMPORT = (
    "grants are deliberately never exported or imported: a grant is a live "
    "capability, so restoring one would republish a system from a backup"
)
# The Article 20 export is system content restored INTO an already-existing
# account, so no column of the account row itself round-trips.
_ACCOUNT_CREDENTIAL = (
    "credential material; exporting it would turn a portable file into an "
    "account-takeover kit"
)
_ACCOUNT_ENTITLEMENT = (
    "server-granted entitlement, set by the operator on this instance; "
    "importing one would be privilege escalation by file"
)
_ACCOUNT_AUTH_STATE = (
    "auth-flow bookkeeping for this instance's account row, not portable "
    "content"
)
_ACCOUNT_SERVER_STATE = (
    "server-derived account state, held on the instance that derived it; "
    "the Article 15 bundle covers it, the portable export does not"
)


# ---------------------------------------------------------------------------
# Classification. Per model: every ORM column must appear in exactly one of
# `exported` (set) or `excluded` (col -> reason). Keep these in sync with
# export.py / sheaf_import.py - that's the whole point.
# ---------------------------------------------------------------------------

CLASSIFICATION: dict[type, dict] = {
    # The account row. Nothing on it round-trips: an export is restored into an
    # account that already exists, so every column here is either credential
    # material, an operator-granted entitlement, or state the receiving
    # instance derives for itself. Listed in full anyway, so a new User column
    # fails this guard until someone states which of those it is - the whole
    # point of the classification.
    User: {
        "exported": set(),
        "excluded": {
            "id": (
                "the importing account's own id; an export never creates an "
                "account"
            ),
            "email": "account identity, set at signup on the receiving instance",
            "email_hash": "derived blind index of email, recomputed at signup",
            "password_hash": _ACCOUNT_CREDENTIAL,
            "totp_secret": _ACCOUNT_CREDENTIAL,
            "totp_enabled": _ACCOUNT_CREDENTIAL,
            "recovery_codes": _ACCOUNT_CREDENTIAL,
            "is_admin": _ACCOUNT_ENTITLEMENT,
            "can_upload_images": _ACCOUNT_ENTITLEMENT,
            "can_upload_animated_images": _ACCOUNT_ENTITLEMENT,
            "member_limit": _ACCOUNT_ENTITLEMENT,
            "tier": _ACCOUNT_ENTITLEMENT,
            "account_status": "moderation state, local to the instance that set it",
            "suspended_until": "moderation state, local to the instance that set it",
            "suspended_reason": "moderation state, local to the instance that set it",
            "email_verified": _ACCOUNT_AUTH_STATE,
            "email_verification_token": _ACCOUNT_AUTH_STATE,
            "email_verification_sent_at": _ACCOUNT_AUTH_STATE,
            "password_reset_token": _ACCOUNT_AUTH_STATE,
            "password_reset_sent_at": _ACCOUNT_AUTH_STATE,
            "invite_code_id": "signup provenance FK, meaningless elsewhere",
            "signup_ip": (
                "IP telemetry; the portable export excludes IPs by contract "
                "because it is shareable"
            ),
            "last_login_at": _ACCOUNT_SERVER_STATE,
            "failed_login_count": _ACCOUNT_SERVER_STATE,
            "locked_until": _ACCOUNT_SERVER_STATE,
            "deletion_requested_at": (
                "pending-deletion clock; a restore must never re-arm a "
                "deletion the user is trying to escape"
            ),
            "deletion_reminders_sent": (
                "reminder bookkeeping for the pending-deletion clock"
            ),
            "newsletter_opt_in": (
                "marketing consent is given to the instance that will send the "
                "mail; it is not transferable, so it is re-asked, never imported"
            ),
            "newsletter_opted_in_at": (
                "timestamp of a consent that is deliberately not transferable"
            ),
            "email_delivery_status": _ACCOUNT_SERVER_STATE,
            "email_delivery_status_changed_at": _ACCOUNT_SERVER_STATE,
            "email_soft_bounce_count": _ACCOUNT_SERVER_STATE,
            "email_revalidation_required": _ACCOUNT_SERVER_STATE,
            "adult_attested_at": (
                "the age attestation that gates publishing. Grants never "
                "round-trip, so a restored account is exposing nothing and "
                "must deliberately re-attest before it can publish again - "
                "importing the attestation would carry a legal declaration "
                "across accounts and instances on a file's say-so"
            ),
            "disable_cdn_during_ddos": (
                "shield-mode opt-out, meaningful only on an instance with a "
                "CDN break-glass setup"
            ),
            "created_at": _ROW_CREATED,
            "updated_at": _ROW_UPDATED,
        },
    },
    System: {
        "exported": {
            "name", "description", "note", "tag", "avatar_url", "color",
            "privacy", "delete_confirmation", "date_format", "timezone",
            "replace_fronts_default", "coalesce_contiguous_fronts",
            "show_member_created_date",
            "auto_pin_first_revision", "safety_grace_period_days",
            "safety_applies_to_members", "safety_applies_to_groups",
            "safety_applies_to_tags", "safety_applies_to_fields",
            "safety_applies_to_fronts", "safety_applies_to_journals",
            "safety_applies_to_images", "safety_applies_to_revisions",
            "safety_applies_to_notifications", "safety_applies_to_reminders",
            "safety_applies_to_polls", "safety_applies_to_messages",
            "safety_applies_to_relationships",
            "safety_applies_to_archive", "safety_applies_to_profile_visibility",
            "journal_max_revisions", "journal_max_revision_days",
            "pinned_revision_max_per_target", "openplural_archive",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "user_id": "owning account FK, re-pointed to the importing user",
            "created_at": _ROW_CREATED,
            "updated_at": _ROW_UPDATED,
            "front_retention_days": (
                "opt-in privacy setting that arms deletion; deliberately not "
                "round-tripped so a restore never silently arms a deletion "
                "policy - the user re-enables it through the guarded flow"
            ),
            "pending_privacy": _SYSTEM_RAISE_STAGING,
            "privacy_activates_at": _SYSTEM_RAISE_STAGING,
            "publishing_blocked": (
                "operator-imposed takedown latch, set and cleared only by an "
                "admin on this instance; carrying it across a restore would let "
                "a file re-impose or lift a moderation action"
            ),
        },
    },
    Member: {
        "exported": {
            "name", "display_name", "description", "pronouns", "avatar_url",
            "banner_url",
            "color", "birthday", "pluralkit_id", "emoji", "is_custom_front",
            "privacy", "note", "quick_switch_pin", "created_at", "archived_at",
            "notify_on_front_global", "notify_on_front_self",
            "notify_on_front_member_ids",
            # Protective share guards. Round-tripped so a restore never comes
            # back less protected than the backup was.
            "never_shareable", "fronting_private",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "name_hash": "derived blind index of name, recomputed on import",
            "updated_at": _ROW_UPDATED,
            "fronting_private_activates_at": (
                "live System Safety staging state; imports restore the guard "
                "itself but never resume an in-flight release"
            ),
        },
    },
    Front: {
        "exported": {"started_at", "ended_at", "custom_status"},
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            # created_at is local row-provenance: a re-imported front is a new
            # row and gets a fresh created_at (the server default), so it is
            # deliberately not carried in the export.
            "created_at": _ROW_CREATED,
            # member_ids ride the front_members association, exported as a list.
        },
    },
    Group: {
        "exported": {"name", "description", "color", "parent_id", "privacy"},
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "created_at": _ROW_CREATED,
            "updated_at": _ROW_UPDATED,
            "pending_privacy": _GROUP_RAISE_STAGING,
            "privacy_activates_at": _GROUP_RAISE_STAGING,
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
    # Share views round-trip (they are real curation work the user did).
    # Share GRANTS deliberately do not - see the ShareGrant entry below.
    ShareView: {
        "exported": {
            "name", "include_members", "include_bio", "include_fronting",
            "fronting_show_count", "include_relationships", "include_groups",
            # Not a staged flag (it exposes nothing new, so it never waits),
            # but it IS the owner's setting, so it round-trips like the rest.
            "member_permalinks",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "created_at": _ROW_CREATED,
            "updated_at": _ROW_UPDATED,
            "pending_include_bio": _SHARE_FLAG_STAGING,
            "pending_include_fronting": _SHARE_FLAG_STAGING,
            "pending_fronting_show_count": _SHARE_FLAG_STAGING,
            "pending_include_relationships": _SHARE_FLAG_STAGING,
            "pending_include_members": _SHARE_FLAG_STAGING,
            "pending_include_groups": _SHARE_FLAG_STAGING,
            "flags_activate_at": _SHARE_FLAG_STAGING,
        },
    },
    ShareViewMember: {
        # `added_via_group_id` rides along as the view's `member_sources` map
        # (old member uuid -> old group uuid), remapped on import through the
        # same old->new group map the view's `group_ids` use. It is what makes
        # detaching a group remove the members that group added rather than its
        # current roster, so a restored backup that lost it would detach the
        # wrong people - a live privacy behaviour, not bookkeeping.
        "exported": {"member_id", "added_via_group_id"},
        "excluded": {
            "id": _SURROGATE_PK,
            "view_id": "parent view FK, re-pointed via the old->new view map",
            "status": _SHARE_LIFECYCLE,
            "activates_at": _SHARE_LIFECYCLE,
            "created_at": _ROW_CREATED,
        },
    },
    ShareViewField: {
        "exported": {"field_id"},
        "excluded": {
            "id": _SURROGATE_PK,
            "view_id": "parent view FK, re-pointed via the old->new view map",
            "status": _SHARE_LIFECYCLE,
            "activates_at": _SHARE_LIFECYCLE,
            "created_at": _ROW_CREATED,
        },
    },
    ShareViewGroup: {
        "exported": {"group_id"},
        "excluded": {
            "id": _SURROGATE_PK,
            "view_id": "parent view FK, re-pointed via the old->new view map",
            "synced_at": (
                "provenance bookkeeping for the last group expansion, not "
                "portable content"
            ),
            "created_at": _ROW_CREATED,
        },
    },
    ShareGrant: {
        # Nothing here round-trips, on purpose. A grant is a LIVE CAPABILITY:
        # re-creating one on import would republish a system straight out of a
        # restored backup, which is the worst possible outcome for a feature
        # whose threat model is accidental outing. Link tokens could not be
        # restored anyway (only a keyed hash is ever stored). After a restore
        # the user's views are intact and nothing is published until they
        # deliberately publish it again.
        "exported": set(),
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "view_id": _NO_GRANT_IMPORT,
            "subject_type": _NO_GRANT_IMPORT,
            "token_hash": (
                "keyed HMAC of a bearer capability; never exported, and the "
                "raw token exists only at creation time"
            ),
            "note": _NO_GRANT_IMPORT,
            "status": _NO_GRANT_IMPORT,
            "activates_at": _NO_GRANT_IMPORT,
            "expires_at": _NO_GRANT_IMPORT,
            "revoked_at": _NO_GRANT_IMPORT,
            "created_at": _ROW_CREATED,
            "created_by_user_id": _NO_GRANT_IMPORT,
        },
    },
    CustomFieldDefinition: {
        "exported": {"name", "field_type", "options", "order", "privacy"},
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "created_at": _ROW_CREATED,
            "updated_at": _ROW_UPDATED,
            "pending_privacy": _FIELD_RAISE_STAGING,
            "privacy_activates_at": _FIELD_RAISE_STAGING,
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
            # inserted_at is local row-provenance: a re-imported revision is a
            # new row and gets a fresh inserted_at (the server default), so it
            # is deliberately not carried in the export. created_at still
            # round-trips the source edit time; inserted_at tracks when the row
            # landed here (what the retention sweep counts from). Same rationale
            # as a fronts row created_at.
            "inserted_at": _ROW_CREATED,
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
    RelationshipType: {
        "exported": {
            "name", "symmetry", "forward_label", "reverse_label", "color",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "created_at": _ROW_CREATED,
            "updated_at": _ROW_UPDATED,
        },
    },
    MemberRelationship: {
        # source_id/target_id/relationship_type_id carry the OLD uuids and are
        # remapped onto the new member + type rows on import, exactly like
        # CustomFieldValue.member_id.
        "exported": {
            "source_id", "target_id", "relationship_type_id", "mutual",
            "visibility", "created_at",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
            "pending_visibility": _EDGE_RAISE_STAGING,
            "visibility_activates_at": _EDGE_RAISE_STAGING,
        },
    },
    GroupRelationship: {
        "exported": {
            "source_id", "target_id", "relationship_type_id", "mutual",
            "visibility", "created_at",
        },
        "excluded": {
            "id": _SURROGATE_PK,
            "system_id": _TENANT_FK,
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
