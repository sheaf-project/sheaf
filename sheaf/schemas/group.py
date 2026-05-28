import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("name")
    @classmethod
    def _reject_explicit_null(cls, v):
        if v is None:
            raise ValueError("cannot be null")
        return v


class GroupRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    name: str
    description: str | None
    color: str | None
    parent_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    # finalize_after timestamp if queued for delete; null otherwise.
    pending_delete_at: datetime | None = None

    model_config = {"from_attributes": True}


class GroupMemberUpdate(BaseModel):
    member_ids: list[uuid.UUID]
