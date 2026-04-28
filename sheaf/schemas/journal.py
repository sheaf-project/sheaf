import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer, field_validator

from sheaf.files import normalize_description_urls, resolve_description_urls

# v1 only honors "system". The other values are reserved for forward
# compatibility (per-member auth and public profiles are far-future).
_ALLOWED_VISIBILITY_V1 = {"system"}


class JournalEntryCreate(BaseModel):
    member_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1)
    visibility: str = Field(default="system", max_length=16)
    author_member_ids: list[uuid.UUID] | None = None

    @field_validator("body", mode="before")
    @classmethod
    def _normalize_body(cls, v: str | None) -> str | None:
        return normalize_description_urls(v)

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, v: str) -> str:
        if v not in _ALLOWED_VISIBILITY_V1:
            raise ValueError(
                f"visibility must be one of {sorted(_ALLOWED_VISIBILITY_V1)}"
            )
        return v


class JournalEntryUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, min_length=1)
    visibility: str | None = Field(default=None, max_length=16)
    author_member_ids: list[uuid.UUID] | None = None

    @field_validator("body", mode="before")
    @classmethod
    def _normalize_body(cls, v: str | None) -> str | None:
        return normalize_description_urls(v)

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in _ALLOWED_VISIBILITY_V1:
            raise ValueError(
                f"visibility must be one of {sorted(_ALLOWED_VISIBILITY_V1)}"
            )
        return v


class JournalEntryDeleteConfirm(BaseModel):
    password: str | None = None
    totp_code: str | None = None


class JournalEntryRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    member_id: uuid.UUID | None
    title: str | None
    body: str
    visibility: str
    author_user_id: uuid.UUID | None
    author_member_ids: list[str]
    author_member_names: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("body")
    def _sign_body_urls(self, v: str) -> str:
        return resolve_description_urls(v) or v


class JournalEntryReadWithCount(JournalEntryRead):
    revision_count: int = 0


class JournalListResponse(BaseModel):
    items: list[JournalEntryRead]
    next_cursor: datetime | None = None


class ContentRevisionRead(BaseModel):
    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    user_id: uuid.UUID | None
    editor_member_ids: list[str]
    editor_member_names: list[str]
    title: str | None
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("body")
    def _sign_body_urls(self, v: str) -> str:
        return resolve_description_urls(v) or v


class RestoreRevisionRequest(BaseModel):
    revision_id: uuid.UUID
