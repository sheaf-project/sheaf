"""Pydantic models for SimplyPlural data import."""

from pydantic import BaseModel, ConfigDict, Field


class SPImportOptions(BaseModel):
    """What to import from the SP export."""

    # Strict: a typo'd option key from a hand-rolled client 422s rather
    # than being silently ignored.
    model_config = ConfigDict(extra="forbid")

    system_profile: bool = True
    member_ids: list[str] | None = Field(
        None, max_length=10_000, description="SP member IDs to import. None = all."
    )
    custom_fronts: bool = True
    custom_fields: bool = True
    groups: bool = True
    front_history: bool = False  # Default off — can be huge
    notes: bool = False  # Maps to future journal feature


class SPPreviewMember(BaseModel):
    id: str
    name: str


class SPPreviewCustomFront(BaseModel):
    id: str
    name: str


class SPPreviewSummary(BaseModel):
    system_name: str | None = None
    member_count: int = 0
    members: list[SPPreviewMember] = []
    custom_front_count: int = 0
    custom_fronts: list[SPPreviewCustomFront] = []
    front_history_count: int = 0
    group_count: int = 0
    custom_field_count: int = 0
    note_count: int = 0


class SPImportResult(BaseModel):
    members_imported: int = 0
    custom_fronts_imported: int = 0
    fronts_imported: int = 0
    groups_imported: int = 0
    custom_fields_imported: int = 0
    notes_skipped: int = 0  # Until journal feature exists
    warnings: list[str] = []
