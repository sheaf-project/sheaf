import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class GroupCreate(BaseModel):
    name: str = Field(max_length=100)
    description: str | None = None
    color: str | None = Field(default=None, max_length=7)
    parent_id: uuid.UUID | None = None


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    color: str | None = Field(default=None, max_length=7)
    parent_id: uuid.UUID | None = None


class GroupRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    name: str
    description: str | None
    color: str | None
    parent_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GroupMemberUpdate(BaseModel):
    member_ids: list[uuid.UUID]
