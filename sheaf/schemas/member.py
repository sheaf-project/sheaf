import uuid
from datetime import datetime

from pydantic import BaseModel, Field

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


class MemberUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    display_name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    pronouns: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=7)
    birthday: str | None = Field(default=None, max_length=10)
    privacy: PrivacyLevel | None = None


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
