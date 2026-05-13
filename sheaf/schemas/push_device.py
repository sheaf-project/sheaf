"""Pydantic schemas for the mobile push device-token endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from sheaf.models.push_device_token import PushPlatform


class PushDeviceRegisterRequest(BaseModel):
    platform: PushPlatform
    token: str = Field(min_length=1, max_length=4096)
    install_id: str | None = Field(default=None, max_length=64)
    app_version: str | None = Field(default=None, max_length=32)
    label: str | None = Field(default=None, max_length=80)


class PushDeviceUpdateRequest(BaseModel):
    """PATCH body for /v1/devices/push/{id}. Owner-only fields the
    recipient toggles from the Receiving tab — does not accept token
    or platform (those rotate via the register endpoint)."""

    enabled: bool | None = None
    label: str | None = Field(default=None, max_length=80)


class PushDeviceDeleteRequest(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


class PushDeviceRead(BaseModel):
    id: uuid.UUID
    platform: PushPlatform
    label: str | None
    enabled: bool
    install_id: str | None
    app_version: str | None
    last_seen_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True
