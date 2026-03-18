import uuid
from datetime import datetime

from pydantic import BaseModel


class FrontCreate(BaseModel):
    member_ids: list[uuid.UUID]
    started_at: datetime | None = None


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
