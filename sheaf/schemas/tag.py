import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class TagCreate(BaseModel):
    name: str = Field(max_length=50)
    color: str | None = Field(default=None, max_length=7)


class TagUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=50)
    color: str | None = Field(default=None, max_length=7)

    @field_validator("name")
    @classmethod
    def _reject_explicit_null(cls, v):
        if v is None:
            raise ValueError("cannot be null")
        return v


class TagRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    name: str
    color: str | None
    created_at: datetime
    updated_at: datetime
    # finalize_after timestamp if this tag is queued for delete in System
    # Safety's grace window; null otherwise. Drives the pending-delete
    # badge + dim styling in list views.
    pending_delete_at: datetime | None = None

    model_config = {"from_attributes": True}


class TagMemberUpdate(BaseModel):
    member_ids: list[uuid.UUID]
