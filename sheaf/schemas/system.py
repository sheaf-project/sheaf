import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer, field_validator

from sheaf.files import (
    normalize_avatar_url,
    normalize_description_urls,
    resolve_avatar_url,
    resolve_description_urls,
)
from sheaf.models.system import DateFormat, DeleteConfirmation, PrivacyLevel


class SystemCreate(BaseModel):
    name: str = Field(max_length=100)
    description: str | None = None
    note: str | None = Field(default=None, max_length=5000)
    tag: str | None = Field(default=None, max_length=8)
    avatar_url: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=7)
    privacy: PrivacyLevel = PrivacyLevel.PRIVATE

    @field_validator("avatar_url", mode="before")
    @classmethod
    def _normalize_avatar(cls, v: str | None) -> str | None:
        return normalize_avatar_url(v)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, v: str | None) -> str | None:
        return normalize_description_urls(v)


class SystemUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    note: str | None = Field(default=None, max_length=5000)
    tag: str | None = Field(default=None, max_length=8)
    avatar_url: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=7)
    privacy: PrivacyLevel | None = None
    date_format: DateFormat | None = None
    replace_fronts_default: bool | None = None
    coalesce_contiguous_fronts: bool | None = None

    @field_validator("avatar_url", mode="before")
    @classmethod
    def _normalize_avatar(cls, v: str | None) -> str | None:
        return normalize_avatar_url(v)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, v: str | None) -> str | None:
        return normalize_description_urls(v)


class SystemRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    note: str | None
    tag: str | None
    avatar_url: str | None
    color: str | None
    privacy: PrivacyLevel
    delete_confirmation: DeleteConfirmation
    date_format: DateFormat
    replace_fronts_default: bool
    coalesce_contiguous_fronts: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("avatar_url")
    def _sign_avatar_url(self, v: str | None) -> str | None:
        return resolve_avatar_url(v)

    @field_serializer("description")
    def _sign_description_urls(self, v: str | None) -> str | None:
        return resolve_description_urls(v)


class DeleteConfirmationUpdate(BaseModel):
    level: DeleteConfirmation
    password: str
    totp_code: str | None = None
