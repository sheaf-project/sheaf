import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

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

    model_config = {"from_attributes": True}


class CustomFieldValueSet(BaseModel):
    field_id: uuid.UUID
    value: Any


class CustomFieldValueRead(BaseModel):
    field_id: uuid.UUID
    member_id: uuid.UUID
    value: Any

    model_config = {"from_attributes": True}
