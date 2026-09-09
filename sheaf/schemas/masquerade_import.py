"""Pydantic models for Masquerade data import.

Masquerade is a proxy bot for Stoat. Its export is a flat list of
"profiles" (member-equivalents) carrying identity data only: no
system-level metadata, no groups, no fronting history, no custom
fields. The schemas here mirror that smaller surface.
"""

from pydantic import BaseModel, ConfigDict, Field

from sheaf.services.import_dedup import ImportConflictStrategy


class MasqueradeImportOptions(BaseModel):
    """What to import from a Masquerade export."""

    # Strict: a typo'd option key from a hand-rolled client 422s rather
    # than being silently ignored.
    model_config = ConfigDict(extra="forbid")

    conflict_strategy: ImportConflictStrategy = ImportConflictStrategy.SKIP
    member_ids: list[str] | None = Field(
        default=None,
        max_length=10_000,
        description=(
            "Masquerade profile positions (array indexes as strings) to "
            "import. None = all. Profiles carry no id of their own, so the "
            "preview keys them by position; used to let the user deselect "
            "specific profiles on the preview screen."
        ),
    )


class MasqueradePreviewMember(BaseModel):
    id: str  # position in the profiles array, stringified for transport
    name: str


class MasqueradePreviewSummary(BaseModel):
    member_count: int = 0
    members: list[MasqueradePreviewMember] = []
    # Fields that exceed the schema caps and would be shortened on import
    # (so the user can cancel or continue). Mirrors what run_import's clamp
    # pass would record.
    limit_warnings: list[str] = []


class MasqueradeImportResult(BaseModel):
    members_imported: int = 0
    members_skipped: int = 0
    members_updated: int = 0
    # Members whose privacy the file raised to public but the import kept as
    # it was, because publishing an already-shared member needs step-up
    # re-auth an import job cannot ask for.
    members_privacy_skipped: int = 0
    warnings: list[str] = []
