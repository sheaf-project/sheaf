import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class AnnouncementCreate(BaseModel):
    title: str = Field(max_length=200)
    body: str = Field(max_length=2000)
    severity: str = Field(default="info", pattern=r"^(info|warning|critical)$")
    dismissible: bool = True
    active: bool = True
    visible_while_logged_out: bool = False
    starts_at: datetime | None = None
    expires_at: datetime | None = None


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=2000)
    severity: str | None = Field(default=None, pattern=r"^(info|warning|critical)$")
    dismissible: bool | None = None
    active: bool | None = None
    visible_while_logged_out: bool | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    clear_starts_at: bool = False
    clear_expires_at: bool = False

    # NOT-NULL columns on the model; the `| None` annotation only exists
    # so model_fields_set can distinguish "omitted" from "supplied" for
    # PATCH semantics. Reject explicit nulls before they reach the DB.
    @field_validator(
        "title", "body", "severity", "dismissible", "active", "visible_while_logged_out"
    )
    @classmethod
    def _reject_explicit_null(cls, v):
        if v is None:
            raise ValueError("cannot be null")
        return v


class AnnouncementPublic(BaseModel):
    """Public-facing schema — no admin metadata."""

    id: uuid.UUID
    title: str
    body: str
    severity: str
    dismissible: bool
    starts_at: datetime | None
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AnnouncementRead(AnnouncementPublic):
    """Admin schema — includes internal fields."""

    active: bool
    visible_while_logged_out: bool
    created_by: uuid.UUID | None
    updated_at: datetime
