import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from sheaf.models.custom_field import FieldType
from sheaf.models.system import PrivacyLevel


class CustomFieldCreate(BaseModel):
    name: str = Field(max_length=100)
    field_type: FieldType
    options: dict | None = None
    order: int = 0
    privacy: PrivacyLevel = PrivacyLevel.PRIVATE


class CustomFieldUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    options: dict | None = None
    order: int | None = None
    privacy: PrivacyLevel | None = None

    @field_validator("name", "order", "privacy")
    @classmethod
    def _reject_explicit_null(cls, v):
        if v is None:
            raise ValueError("cannot be null")
        return v


class CustomFieldRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    name: str
    field_type: FieldType
    options: dict | None
    order: int
    privacy: PrivacyLevel
    created_at: datetime
    updated_at: datetime
    # finalize_after timestamp if queued for delete; null otherwise.
    pending_delete_at: datetime | None = None

    model_config = {"from_attributes": True}


class CustomFieldValueSet(BaseModel):
    field_id: uuid.UUID
    value: Any


class CustomFieldValueRead(BaseModel):
    field_id: uuid.UUID
    member_id: uuid.UUID
    value: Any

    model_config = {"from_attributes": True}
