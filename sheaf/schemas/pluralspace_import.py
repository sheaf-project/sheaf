"""Pydantic schemas for the PluralSpace importer.

PluralSpace exports as a GDPR-style ZIP containing `manifest.json` +
`data.json` (the parsed entity payload, format_version "1.x") plus a
`media/` directory of embedded avatar files. Preview parses the zip
and returns a summary; the full import reads it back from storage,
walks `data.json`, and re-uploads referenced media as the importing
user's avatars.

`extra="forbid"` on the options model: typos in option keys should
fail loud rather than silently revert to defaults. The preview /
result models leave extras open for forward-compat with future
format_version bumps.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from sheaf.services.import_dedup import ImportConflictStrategy


class PluralspaceImportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_strategy: ImportConflictStrategy = ImportConflictStrategy.SKIP
    system_profile: bool = True
    member_ids: list[str] | None = None

    custom_fronts: bool = True
    member_avatars: bool = True

    roles_as_tags: bool = True
    groups: bool = True
    custom_fields: bool = True

    fronts: bool = True
    journal_entries: bool = True
    chat_messages: bool = True
    polls: bool = True


class PluralspacePreviewMember(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    is_custom_front: bool = False
    is_archived: bool = False
    has_avatar: bool = False
    roles: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)


class PluralspacePreviewSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    system_name: str | None = None
    format_version: str | None = None
    export_date: datetime | None = None

    member_count: int = 0
    custom_front_count: int = 0
    members: list[PluralspacePreviewMember] = Field(default_factory=list)

    group_count: int = 0
    custom_field_count: int = 0
    front_count: int = 0
    journal_entry_count: int = 0
    chat_channel_count: int = 0
    chat_message_count: int = 0
    poll_count: int = 0
    thought_count: int = 0
    media_file_count: int = 0


class PluralspaceImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    members_imported: int = 0
    custom_fronts_imported: int = 0
    # Dedup dispositions, covering all roster rows (members + custom
    # fronts) that matched an existing row instead of being created.
    members_skipped: int = 0
    members_updated: int = 0
    avatars_imported: int = 0
    tags_imported: int = 0
    groups_imported: int = 0
    custom_fields_imported: int = 0
    fronts_imported: int = 0
    journals_imported: int = 0
    messages_imported: int = 0
    polls_imported: int = 0
    warnings: list[str] = Field(default_factory=list)
