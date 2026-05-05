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
    # Per-member effective "fronting since" timestamp, keyed by member id
    # (string form). When the system has `coalesce_contiguous_fronts` on
    # AND the member appears in a chain of back-to-back front entries
    # ending in this one, this is the earliest started_at in the chain.
    # Otherwise it's the literal `started_at` of this entry. Only walked
    # back for open fronts on /v1/fronts/current; closed fronts (history)
    # always carry the literal value.
    member_since: dict[str, datetime] = {}
    # Members whose walk-back hit the safety depth cap. The corresponding
    # `member_since` entry is a lower bound, not the true chain start.
    # UIs should render these with a "> X ago" prefix. Empty in the
    # overwhelming majority of cases — chains are typically 1-3 entries.
    member_since_capped: list[str] = []

    model_config = {"from_attributes": True}
