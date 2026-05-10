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


class PushDeviceDeleteRequest(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


class PushDeviceRead(BaseModel):
    id: uuid.UUID
    platform: PushPlatform
    install_id: str | None
    app_version: str | None
    last_seen_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True
