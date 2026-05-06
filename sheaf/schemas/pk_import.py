"""Pydantic models for PluralKit data import.

Both ingestion paths (file upload and live API pull) produce the same
canonical PK-shaped dict, so a single set of schemas describes the
preview/options/result regardless of source.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class PKImportOptions(BaseModel):
    """What to import from a PluralKit export."""

    system_profile: bool = True
    member_ids: list[str] | None = Field(
        default=None,
        description=(
            "PK member HIDs to import. None = all. Used to let the user "
            "deselect specific members on the preview screen."
        ),
    )
    groups: bool = True
    front_history: bool = False  # Default off — switch logs can run thousands of entries.


class PKApiCredentials(BaseModel):
    """Token-based credentials for the live PluralKit API.

    The token is request-scoped: it lives only for the duration of the
    request handler and is never logged or persisted. The frontend
    submits it via HTTPS POST body.
    """

    token: str = Field(min_length=1, max_length=128)


class PKApiPreviewRequest(PKApiCredentials):
    pass


class PKApiImportRequest(PKApiCredentials):
    options: PKImportOptions = Field(default_factory=PKImportOptions)


class PKPreviewMember(BaseModel):
    id: str  # PK HID
    name: str


class PKPreviewSummary(BaseModel):
    system_name: str | None = None
    member_count: int = 0
    members: list[PKPreviewMember] = []
    group_count: int = 0
    switch_count: int = 0
    earliest_switch: datetime | None = None
    latest_switch: datetime | None = None


class PKImportResult(BaseModel):
    members_imported: int = 0
    groups_imported: int = 0
    fronts_imported: int = 0
    warnings: list[str] = []
