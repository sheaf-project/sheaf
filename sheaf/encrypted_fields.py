"""Per-cell AAD helpers for field encryption - the encrypted-cell registry.

Every encrypted column in the database has exactly one helper here that
produces the AAD binding a ciphertext to its cell (see `crypto.field_aad`).
Call sites must use these helpers rather than calling `field_aad` directly so
the (table, column) pair for each cell is written once, in one reviewable
place, and cannot drift between the encrypt and decrypt side of a field.

This module is deliberately the complete inventory of encrypted cells: the
Phase 2 re-encrypt sweep and the Phase 3 "cells remaining on v1" preflight
enumerate their work from it. If you add an encrypted column, add its helper
here and wire it into the sweep, or the column stays on the legacy format
forever and blocks the v1 removal.

The registry alone is NOT the whole sweep surface: `CIPHERTEXT_COPIES` below
lists places that store a copy of some cell's ciphertext outside that cell
(currently the front audit snapshots). A sweep driven only by the helpers
would miss those copies and leave a v1 long tail that breaks when v1 reads
are disabled.

Conventions:

- `pk` is the owning row's UUID. All encrypted columns live on single-UUID-PK
  tables. On INSERT paths the id does not exist until flush, so the call site
  pre-allocates it (`row_id = uuid.uuid4()`) and passes the same value to both
  the model constructor and the helper - the pattern import_jobs already uses.
- JSON-embedded ciphertexts use a fixed logical column path (e.g.
  `payload_metadata.encrypted_credential`). These strings are baked into the
  AAD of stored rows: never rename them.
- `content_revisions` ciphertexts are bound to the *revision* row's own id,
  not the revised target, so revisions of the same entry are not mutually
  swappable at the DB layer.
"""

from sheaf.crypto import field_aad

# users

def user_email_aad(user_id) -> bytes:
    return field_aad("users", "email", user_id)


def user_totp_secret_aad(user_id) -> bytes:
    return field_aad("users", "totp_secret", user_id)


def user_recovery_codes_aad(user_id) -> bytes:
    return field_aad("users", "recovery_codes", user_id)


# members

def member_name_aad(member_id) -> bytes:
    return field_aad("members", "name", member_id)


def member_description_aad(member_id) -> bytes:
    return field_aad("members", "description", member_id)


def member_note_aad(member_id) -> bytes:
    return field_aad("members", "note", member_id)


# systems

def system_note_aad(system_id) -> bytes:
    return field_aad("systems", "note", system_id)


def system_openplural_archive_aad(system_id) -> bytes:
    return field_aad("systems", "openplural_archive", system_id)


# fronts

def front_custom_status_aad(front_id) -> bytes:
    return field_aad("fronts", "custom_status", front_id)


# journal_entries

def journal_title_aad(entry_id) -> bytes:
    return field_aad("journal_entries", "title", entry_id)


def journal_body_aad(entry_id) -> bytes:
    return field_aad("journal_entries", "body", entry_id)


# content_revisions (bound to the revision row, see module docstring)

def revision_title_aad(revision_id) -> bytes:
    return field_aad("content_revisions", "title", revision_id)


def revision_body_aad(revision_id) -> bytes:
    return field_aad("content_revisions", "body", revision_id)


# reminders

def reminder_title_aad(reminder_id) -> bytes:
    return field_aad("reminders", "title", reminder_id)


def reminder_body_aad(reminder_id) -> bytes:
    return field_aad("reminders", "body", reminder_id)


# messages

def message_body_aad(message_id) -> bytes:
    return field_aad("messages", "body", message_id)


# polls / poll_options

def poll_question_aad(poll_id) -> bytes:
    return field_aad("polls", "question", poll_id)


def poll_description_aad(poll_id) -> bytes:
    return field_aad("polls", "description", poll_id)


def poll_option_text_aad(option_id) -> bytes:
    return field_aad("poll_options", "text", option_id)


# custom_field_values (ciphertext stored inside the JSONB `value`)

def custom_field_value_aad(value_id) -> bytes:
    return field_aad("custom_field_values", "value", value_id)


# notification_channels

def webhook_secret_aad(channel_id) -> bytes:
    return field_aad("notification_channels", "webhook_secret_encrypted", channel_id)


# import_jobs (ciphertext inside the payload_metadata JSONB; fixed logical path)

def import_credential_aad(job_id) -> bytes:
    return field_aad("import_jobs", "payload_metadata.encrypted_credential", job_id)


# pending_actions

def pending_target_label_aad(pending_id) -> bytes:
    return field_aad("pending_actions", "target_label", pending_id)


def pending_fronting_names_aad(pending_id) -> bytes:
    return field_aad("pending_actions", "fronting_member_names", pending_id)


# Ciphertext COPIES stored outside their owning cell. Each entry is
# (storage_table, storage_path, source_cell): the stored bytes are a copy of
# the source cell's ciphertext and decrypt under the SOURCE cell's AAD (the
# copy is same-cell by construction - e.g. an audit snapshot of front X's
# custom_status decrypts under front_custom_status_aad(X), and X never
# changes). The Phase 2 sweep must rewrite these alongside the registry
# cells, or the Phase 3 remaining-v1 preflight must count them.
CIPHERTEXT_COPIES = (
    (
        "front_audit_events",
        "before_snapshot.custom_status_encrypted",
        "fronts.custom_status",
    ),
    (
        "front_audit_events",
        "after_snapshot.custom_status_encrypted",
        "fronts.custom_status",
    ),
)
