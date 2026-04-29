import uuid
from datetime import datetime

from pydantic import BaseModel, Field


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
