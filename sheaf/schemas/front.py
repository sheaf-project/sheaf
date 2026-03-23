import uuid
from datetime import datetime

from pydantic import BaseModel


class FrontCreate(BaseModel):
    member_ids: list[uuid.UUID]
    started_at: datetime | None = None
    replace_fronts: bool | None = None  # None = use system's replace_fronts_default


class FrontUpdate(BaseModel):
    ended_at: datetime | None = None
    member_ids: list[uuid.UUID] | None = None


class FrontRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None
    member_ids: list[uuid.UUID]

    model_config = {"from_attributes": True}
