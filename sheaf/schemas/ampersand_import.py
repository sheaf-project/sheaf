"""Pydantic models for Ampersand JSON data import.

Ampersand (https://codeberg.org/Ampersand/app) exports a
`{ revision, config, database }` JSON document; `database` is an object
of per-table arrays. See `sheaf/services/ampersand_import.py` for the
mapping and `../sheaf-design-docs/ampersand-import.md` for the format
research note.
"""

from pydantic import BaseModel, ConfigDict, Field

from sheaf.services.import_dedup import ImportConflictStrategy


class AmpersandImportOptions(BaseModel):
    """What to import from the Ampersand export.

    Strict: a typo'd option key from a hand-rolled client 422s rather
    than being silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    # Members always import. The rest are toggleable; the heavy /
    # content sections default on because an Ampersand export is a
    # one-shot migration and the user expects their data to come across.
    conflict_strategy: ImportConflictStrategy = ImportConflictStrategy.SKIP
    member_ids: list[str] | None = Field(
        None, max_length=10_000, description="Ampersand member UUIDs to import. None = all."
    )
    custom_fronts: bool = True
    custom_fields: bool = True
    tags: bool = True
    # Ampersand systems become Sheaf groups (nested via `parent`).
    groups: bool = True
    front_history: bool = True
    journals: bool = True
    notes: bool = True
    board_messages: bool = True
    reminders: bool = True
    # Decode + store the inline base64 avatars / covers. Off falls back
    # to a text-only import (no images), which some users prefer.
    images: bool = True


class AmpersandPreviewSummary(BaseModel):
    system_count: int = 0
    member_count: int = 0
    custom_front_count: int = 0
    front_history_count: int = 0
    tag_count: int = 0
    custom_field_count: int = 0
    journal_count: int = 0
    note_count: int = 0
    board_message_count: int = 0
    poll_count: int = 0
    reminder_count: int = 0
    asset_count: int = 0
    limit_warnings: list[str] = []


class AmpersandImportResult(BaseModel):
    members_imported: int = 0
    custom_fronts_imported: int = 0
    members_skipped: int = 0
    members_updated: int = 0
    groups_imported: int = 0
    groups_skipped: int = 0
    tags_imported: int = 0
    custom_fields_imported: int = 0
    custom_fields_skipped: int = 0
    fronts_imported: int = 0
    fronts_skipped: int = 0
    journals_imported: int = 0
    notes_imported: int = 0
    messages_imported: int = 0
    polls_imported: int = 0
    reminders_imported: int = 0
    images_imported: int = 0
    warnings: list[str] = []
