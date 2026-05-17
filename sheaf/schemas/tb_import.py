"""Pydantic models for Tupperbox data import.

Tupperbox is a Discord proxy bot. Its export covers a flat list of
"tuppers" (member-equivalents) and the groups they belong to. There's
no system-level metadata, no fronting history, no custom fields, and
no privacy model — just identity + grouping data. The schemas here
mirror that smaller surface.
"""

from pydantic import BaseModel, ConfigDict, Field


class TBImportOptions(BaseModel):
    """What to import from a Tupperbox export."""

    # Strict: a typo'd option key from a hand-rolled client 422s rather
    # than being silently ignored.
    model_config = ConfigDict(extra="forbid")

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


class TBImportResult(BaseModel):
    members_imported: int = 0
    groups_imported: int = 0
    warnings: list[str] = []
