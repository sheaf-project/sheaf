"""Pydantic models for Tupperbox data import.

Tupperbox is a Discord proxy bot. Its export covers a flat list of
"tuppers" (member-equivalents) and the groups they belong to. There's
no system-level metadata, no fronting history, no custom fields, and
no privacy model — just identity + grouping data. The schemas here
mirror that smaller surface.
"""

from pydantic import BaseModel, ConfigDict, Field

from sheaf.services.import_dedup import ImportConflictStrategy


class TBImportOptions(BaseModel):
    """What to import from a Tupperbox export."""

    # Strict: a typo'd option key from a hand-rolled client 422s rather
    # than being silently ignored.
    model_config = ConfigDict(extra="forbid")

    conflict_strategy: ImportConflictStrategy = ImportConflictStrategy.SKIP
    member_ids: list[str] | None = Field(
        default=None,
        max_length=10_000,
        description=(
            "Tupperbox tupper IDs (as strings) to import. None = all. Used "
            "to let the user deselect specific tuppers on the preview screen."
        ),
    )
    groups: bool = True


class TBPreviewMember(BaseModel):
    id: str  # Tupperbox numeric id, stringified for transport
    name: str


class TBPreviewSummary(BaseModel):
    member_count: int = 0
    members: list[TBPreviewMember] = []
    group_count: int = 0
    # Fields that exceed the schema caps and would be shortened on import
    # (so the user can cancel or continue). Mirrors what run_import's clamp
    # pass would record.
    limit_warnings: list[str] = []


class TBImportResult(BaseModel):
    members_imported: int = 0
    members_skipped: int = 0
    members_updated: int = 0
    groups_imported: int = 0
    groups_skipped: int = 0
    warnings: list[str] = []
