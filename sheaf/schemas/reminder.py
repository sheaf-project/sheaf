"""Pydantic models for the reminder feature."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# A few sentinel/format constants the API layer reuses.
TRIGGER_TYPE = Literal["automated", "repeated"]
TRIGGER_EVENT = Literal["start", "stop", "any"]
SCHEDULE_KIND = Literal["daily", "weekly", "monthly"]
SCOPE = Literal["system", "member"]


class ReminderBase(BaseModel):
    """Fields shared by create and update payloads."""

    name: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=500)
    body: str | None = Field(default=None, max_length=2000)
    enabled: bool = True
    channel_id: uuid.UUID

    trigger_type: TRIGGER_TYPE

    # automated trigger
    trigger_member_id: uuid.UUID | None = None
    trigger_event: TRIGGER_EVENT | None = None
    delay_seconds: int | None = Field(default=None, ge=0, le=7 * 24 * 3600)

    # repeated structured schedule
    schedule_kind: SCHEDULE_KIND | None = None
    schedule_time: str | None = Field(default=None, pattern=r"^[0-2]\d:[0-5]\d$")
    schedule_dow_mask: int | None = Field(default=None, ge=0, le=127)
    schedule_dom: int | None = Field(default=None, ge=1, le=31)
    schedule_tz: str | None = Field(default=None, max_length=64)

    # repeated advanced cron (takes precedence over structured fields)
    cron_expression: str | None = Field(default=None, max_length=120)

    # scoping (repeated only)
    scope: SCOPE = "system"
    scope_member_ids: list[uuid.UUID] = Field(default_factory=list)
    digest_when_absent: bool = True

    @field_validator("schedule_time")
    @classmethod
    def _check_time_range(cls, v: str | None) -> str | None:
        if v is None:
            return v
        hh, mm = v.split(":")
        if int(hh) > 23 or int(mm) > 59:
            raise ValueError("schedule_time must be 00:00-23:59")
        return v


class ReminderCreate(ReminderBase):
    pass


class ReminderUpdate(BaseModel):
    """Partial update — only the fields that are explicitly supplied
    are written. Member rules are replaced wholesale when supplied;
    omit `scope_member_ids` to leave them alone."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    channel_id: uuid.UUID | None = None

    trigger_type: TRIGGER_TYPE | None = None
    trigger_member_id: uuid.UUID | None = None
    trigger_event: TRIGGER_EVENT | None = None
    delay_seconds: int | None = Field(default=None, ge=0, le=7 * 24 * 3600)

    schedule_kind: SCHEDULE_KIND | None = None
    schedule_time: str | None = Field(default=None, pattern=r"^[0-2]\d:[0-5]\d$")
    schedule_dow_mask: int | None = Field(default=None, ge=0, le=127)
    schedule_dom: int | None = Field(default=None, ge=1, le=31)
    schedule_tz: str | None = Field(default=None, max_length=64)

    cron_expression: str | None = Field(default=None, max_length=120)

    scope: SCOPE | None = None
    scope_member_ids: list[uuid.UUID] | None = None
    digest_when_absent: bool | None = None


class ReminderRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    channel_id: uuid.UUID

    name: str
    title: str
    body: str | None
    enabled: bool
    trigger_type: str

    trigger_member_id: uuid.UUID | None
    trigger_event: str | None
    delay_seconds: int | None

    schedule_kind: str | None
    schedule_time: str | None
    schedule_dow_mask: int | None
    schedule_dom: int | None
    schedule_tz: str | None
    cron_expression: str | None

    scope: str
    scope_member_ids: list[uuid.UUID]
    digest_when_absent: bool

    last_fired_at: datetime | None
    pending_count: int
    next_fire_at: datetime | None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
