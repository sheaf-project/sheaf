"""Pydantic schemas for the Prism (.prism) importer.

Prism exports are encrypted PRISM1 envelopes carrying a JSON
payload (~20 entity types) plus optional XChaCha20-Poly1305 media
attachments. Decryption is server-side; the user submits the
passphrase in an `encrypted_credential` form field that mirrors the
existing PluralKit-API-token pattern, encrypted at rest in
payload_metadata with SHEAF_ENCRYPTION_KEY and wiped at finalize.

`extra="forbid"` on the options model so typos in toggle keys fail
loud. Preview / result models leave extras open for forward-compat
with later format versions.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PrismImportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_profile: bool = True
    member_ids: list[str] | None = None

    member_avatars: bool = True
    roles_as_tags: bool = True
    member_groups: bool = True
    custom_fields: bool = True

    front_sessions: bool = True
    sleep_sessions: bool = True
    notes: bool = True
    polls: bool = True
    reminders: bool = True
    habits: bool = True
    conversations: bool = True
    member_board_posts: bool = True
    media_attachments: bool = True


class PrismPreviewMember(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    is_archived: bool = False
    has_avatar: bool = False
    pluralkit_id: str | None = None


class PrismPreviewSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    system_name: str | None = None
    format_version: str | None = None
    export_date: datetime | None = None
    app_name: str | None = None

    member_count: int = 0
    members: list[PrismPreviewMember] = Field(default_factory=list)

    group_count: int = 0
    custom_field_count: int = 0
    front_session_count: int = 0
    sleep_session_count: int = 0
    conversation_count: int = 0
    message_count: int = 0
    poll_count: int = 0
    poll_option_count: int = 0
    note_count: int = 0
    reminder_count: int = 0
    habit_count: int = 0
    member_board_post_count: int = 0
    media_attachment_count: int = 0
    media_blob_count: int = 0


class PrismImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    members_imported: int = 0
    avatars_imported: int = 0
    groups_imported: int = 0
    custom_fields_imported: int = 0
    custom_field_values_imported: int = 0
    fronts_imported: int = 0
    journals_imported: int = 0
    messages_imported: int = 0
    board_posts_imported: int = 0
    polls_imported: int = 0
    reminders_imported: int = 0
    media_attachments_imported: int = 0
    warnings: list[str] = Field(default_factory=list)
