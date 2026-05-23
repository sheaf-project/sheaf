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
    pluralkit_id: str | None = Field(default=None, max_length=8)
    emoji: str | None = Field(default=None, max_length=8)
    is_custom_front: bool = False
    privacy: PrivacyLevel = PrivacyLevel.PRIVATE
    note: str | None = Field(default=None, max_length=5000)
    quick_switch_pin: int | None = Field(default=None, ge=0)

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
    pluralkit_id: str | None = Field(default=None, max_length=8)
    emoji: str | None = Field(default=None, max_length=8)
    is_custom_front: bool | None = None
    privacy: PrivacyLevel | None = None
    note: str | None = Field(default=None, max_length=5000)
    # Explicit null clears the pin (unpins); omitted leaves it untouched.
    quick_switch_pin: int | None = Field(default=None, ge=0)

    @field_validator("avatar_url", mode="before")
    @classmethod
    def _normalize_avatar(cls, v: str | None) -> str | None:
        return normalize_avatar_url(v)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, v: str | None) -> str | None:
        return normalize_description_urls(v)

    # NOT-NULL columns on the model; `| None` is only here so
    # model_fields_set can distinguish omitted vs supplied.
    @field_validator("name", "is_custom_front", "privacy")
    @classmethod
    def _reject_explicit_null(cls, v):
        if v is None:
            raise ValueError("cannot be null")
        return v


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
    pluralkit_id: str | None
    emoji: str | None
    is_custom_front: bool
    privacy: PrivacyLevel
    note: str | None
    quick_switch_pin: int | None = None
    created_at: datetime
    updated_at: datetime
    # True iff at least one ContentRevision exists for this member's bio.
    # Lets the UI grey out the bio history button on members whose bio
    # has never been edited. Defaults to False for non-list contexts
    # (e.g. nested in tag / group responses) where computing this would
    # be a needless round-trip.
    has_bio_revisions: bool = False

    model_config = {"from_attributes": True}

    @field_serializer("avatar_url")
    def _sign_avatar_url(self, v: str | None) -> str | None:
        return resolve_avatar_url(v)

    @field_serializer("description")
    def _sign_description_urls(self, v: str | None) -> str | None:
        return resolve_description_urls(v)
