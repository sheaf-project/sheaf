import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer

from sheaf.files import resolve_avatar_url
from sheaf.models.system import DateFormat, DeleteConfirmation, PrivacyLevel


class SystemCreate(BaseModel):
    name: str = Field(max_length=100)
    description: str | None = None
    tag: str | None = Field(default=None, max_length=8)
    avatar_url: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=7)
    privacy: PrivacyLevel = PrivacyLevel.PRIVATE


class SystemUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    tag: str | None = Field(default=None, max_length=8)
    avatar_url: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=7)
    privacy: PrivacyLevel | None = None
    date_format: DateFormat | None = None
    replace_fronts_default: bool | None = None


class SystemRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    tag: str | None
    avatar_url: str | None
    color: str | None
    privacy: PrivacyLevel
    delete_confirmation: DeleteConfirmation
    date_format: DateFormat
    replace_fronts_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("avatar_url")
    def _sign_avatar_url(self, v: str | None) -> str | None:
        return resolve_avatar_url(v)


class DeleteConfirmationUpdate(BaseModel):
    level: DeleteConfirmation
    password: str
    totp_code: str | None = None
