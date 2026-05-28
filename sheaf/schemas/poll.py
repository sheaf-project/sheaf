"""Pydantic schemas for the polls feature."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PollKindLiteral = Literal["single_choice", "multi_choice"]
ResultsVisibilityLiteral = Literal["live", "end_only"]
VoteActionLiteral = Literal["cast", "change", "withdraw"]


class PollOptionCreate(BaseModel):
    text: str = Field(min_length=1, max_length=200)


class PollOptionRead(BaseModel):
    id: uuid.UUID
    text: str
    position: int

    model_config = {"from_attributes": True}


class PollCreate(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    kind: PollKindLiteral
    results_visibility: ResultsVisibilityLiteral
    closes_at: datetime
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    include_custom_fronts: bool = False
    options: list[PollOptionCreate] = Field(min_length=2, max_length=20)

    @field_validator("options")
    @classmethod
    def _options_unique(
        cls, v: list[PollOptionCreate]
    ) -> list[PollOptionCreate]:
        seen = {o.text.strip().casefold() for o in v}
        if len(seen) != len(v):
            raise ValueError("option texts must be unique within a poll")
        return v


class PollTallyEntry(BaseModel):
    option_id: uuid.UUID
    count: int


class PollVoteRead(BaseModel):
    voted_as_member_id: uuid.UUID
    option_ids: list[uuid.UUID]
    created_at: datetime
    updated_at: datetime


class PollRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    question: str
    description: str | None
    kind: str
    results_visibility: str
    closes_at: datetime
    retention_days: int
    include_custom_fronts: bool
    options: list[PollOptionRead]

    is_closed: bool
    closed_since: datetime | None
    purges_at: datetime
    total_votes: int

    # Tally is None when results_visibility=end_only and the poll is
    # still open. Otherwise a per-option count.
    tally: list[PollTallyEntry] | None = None
    # Per-(member) vote rows. Only populated for the owner of the
    # system. Same visibility rule as tally.
    votes: list[PollVoteRead] | None = None

    created_at: datetime
    updated_at: datetime
    # finalize_after timestamp if queued for delete; null otherwise.
    pending_delete_at: datetime | None = None

    model_config = {"from_attributes": True}


class VoteCast(BaseModel):
    voted_as_member_id: uuid.UUID
    option_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


class VoteEventRead(BaseModel):
    id: uuid.UUID
    voted_as_member_id: uuid.UUID | None
    action: VoteActionLiteral
    option_ids: list[uuid.UUID]
    fronting_member_ids: list[uuid.UUID]
    actor_user_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PollAuditRead(BaseModel):
    """Audit log for a poll. Same visibility rule as tally — hidden
    until closed when results_visibility=end_only, otherwise live."""

    poll_id: uuid.UUID
    is_visible: bool
    events: list[VoteEventRead]
