import uuid
from datetime import datetime

from pydantic import BaseModel


class ActivityEventRead(BaseModel):
    """A single account-activity row, as shown to the account owner."""

    id: uuid.UUID
    actor_type: str
    action: str
    target_label: str | None
    detail: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}
