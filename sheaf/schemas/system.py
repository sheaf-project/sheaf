import uuid
from datetime import datetime

from pydantic import BaseModel, Field

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
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DeleteConfirmationUpdate(BaseModel):
    level: DeleteConfirmation
    password: str
    totp_code: str | None = None
