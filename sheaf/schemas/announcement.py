import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from sheaf.models.announcement import AnnouncementPlatform

_PLATFORM_VALUES = {p.value for p in AnnouncementPlatform}


def _normalise_platforms(value: list[str] | None) -> list[str] | None:
    """Validate and dedupe a platform list, keeping the caller's order.

    Unknown names are refused rather than dropped: an announcement aimed at
    a misspelt platform would reach nobody, and nothing in the UI would say
    why.
    """
    if value is None:
        return None
    seen: list[str] = []
    for raw in value:
        name = raw.strip().lower()
        if name not in _PLATFORM_VALUES:
            allowed = ", ".join(sorted(_PLATFORM_VALUES))
            raise ValueError(f"unknown platform {raw!r}; expected one of {allowed}")
        if name not in seen:
            seen.append(name)
    return seen


class AnnouncementCreate(BaseModel):
    title: str = Field(max_length=200)
    body: str = Field(max_length=2000)
    severity: str = Field(default="info", pattern=r"^(info|warning|critical)$")
    dismissible: bool = True
    active: bool = True
    visible_while_logged_out: bool = False
    # Empty (the default) means every client. Otherwise only clients asking
    # for one of these platforms see the announcement.
    platforms: list[str] = Field(default_factory=list, max_length=8)
    starts_at: datetime | None = None
    expires_at: datetime | None = None

    @field_validator("platforms")
    @classmethod
    def _check_platforms(cls, v: list[str]) -> list[str]:
        return _normalise_platforms(v) or []


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=2000)
    severity: str | None = Field(default=None, pattern=r"^(info|warning|critical)$")
    dismissible: bool | None = None
    active: bool | None = None
    visible_while_logged_out: bool | None = None
    # Send [] to go back to "every client". Omit to leave unchanged.
    platforms: list[str] | None = Field(default=None, max_length=8)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    clear_starts_at: bool = False
    clear_expires_at: bool = False

    # NOT-NULL columns on the model; the `| None` annotation only exists
    # so model_fields_set can distinguish "omitted" from "supplied" for
    # PATCH semantics. Reject explicit nulls before they reach the DB.
    @field_validator(
        "title",
        "body",
        "severity",
        "dismissible",
        "active",
        "visible_while_logged_out",
        "platforms",
    )
    @classmethod
    def _reject_explicit_null(cls, v):
        if v is None:
            raise ValueError("cannot be null")
        return v

    @field_validator("platforms")
    @classmethod
    def _check_platforms(cls, v: list[str]) -> list[str]:
        return _normalise_platforms(v) or []


class AnnouncementPublic(BaseModel):
    """Public-facing schema - no admin metadata."""

    id: uuid.UUID
    title: str
    body: str
    severity: str
    dismissible: bool
    platforms: list[str]
    starts_at: datetime | None
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AnnouncementRead(AnnouncementPublic):
    """Admin schema - includes internal fields."""

    active: bool
    visible_while_logged_out: bool
    created_by: uuid.UUID | None
    updated_at: datetime
