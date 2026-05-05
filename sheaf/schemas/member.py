import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer, field_validator

from sheaf.files import (
    normalize_avatar_url,
    normalize_description_urls,
    resolve_avatar_url,
    resolve_description_urls,
)
from sheaf.models.system import PrivacyLevel


class MemberCreate(BaseModel):
    name: str = Field(max_length=100)
    display_name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    pronouns: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=7)
    birthday: str | None = Field(default=None, max_length=10)
    privacy: PrivacyLevel = PrivacyLevel.PRIVATE

    @field_validator("avatar_url", mode="before")
    @classmethod
    def _normalize_avatar(cls, v: str | None) -> str | None:
        return normalize_avatar_url(v)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, v: str | None) -> str | None:
        return normalize_description_urls(v)


class MemberUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    display_name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    pronouns: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=7)
    birthday: str | None = Field(default=None, max_length=10)
    privacy: PrivacyLevel | None = None

    @field_validator("avatar_url", mode="before")
    @classmethod
    def _normalize_avatar(cls, v: str | None) -> str | None:
        return normalize_avatar_url(v)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, v: str | None) -> str | None:
        return normalize_description_urls(v)


class MemberDeleteConfirm(BaseModel):
    password: str | None = None
    totp_code: str | None = None


class MemberTagUpdate(BaseModel):
    tag_ids: list[uuid.UUID]


class MemberRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    name: str
    display_name: str | None
    description: str | None
    pronouns: str | None
    avatar_url: str | None
    color: str | None
    birthday: str | None
    privacy: PrivacyLevel
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("avatar_url")
    def _sign_avatar_url(self, v: str | None) -> str | None:
        return resolve_avatar_url(v)

    @field_serializer("description")
    def _sign_description_urls(self, v: str | None) -> str | None:
        return resolve_description_urls(v)
