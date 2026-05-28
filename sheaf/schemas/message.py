"""Pydantic models for the board messages feature."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

BoardKindLiteral = Literal["system", "member"]


class MessageBase(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class MessageCreate(MessageBase):
    board_kind: BoardKindLiteral
    # Required when board_kind="member", forbidden otherwise. Validation
    # in the API layer rather than the schema so the error message can
    # be specific.
    board_member_id: uuid.UUID | None = None
    # The member the post is attributed to. Must be a member of the
    # caller's system; the API rejects ids outside it.
    author_member_id: uuid.UUID
    parent_message_id: uuid.UUID | None = None


class MessageUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class MessageRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    board_kind: str
    board_member_id: uuid.UUID | None
    author_member_id: uuid.UUID | None
    # Snapshotted display name for the author at read time. Renders as
    # `[deleted member]` when the author was removed.
    author_member_name: str | None
    parent_message_id: uuid.UUID | None
    # The body of the parent (truncated) — the UI uses this for the
    # "Replying to X" backlink without a second fetch. None when the
    # parent has been deleted or is missing.
    parent_preview: str | None
    parent_author_member_name: str | None
    body: str
    created_at: datetime
    updated_at: datetime
    # finalize_after timestamp if this message (or its thread) is queued
    # for delete in System Safety's grace window; null otherwise. Unioned
    # across MESSAGE_DELETE and MESSAGE_THREAD_DELETE pending actions.
    pending_delete_at: datetime | None = None

    model_config = {"from_attributes": True}


class MessagesPage(BaseModel):
    """Paginated message list response."""

    board_kind: str
    board_member_id: uuid.UUID | None
    messages: list[MessageRead]
    # The last_seen_at the caller reported (or None if no caller-member
    # was supplied). The caller is the member id passed via query param;
    # see GET /messages docstring.
    caller_last_seen_at: datetime | None = None


class BoardSummary(BaseModel):
    """One entry in the Members tab listing on /messages."""

    board_kind: str
    board_member_id: uuid.UUID | None
    member_name: str | None  # None for the system board entry
    last_message_at: datetime | None
    last_message_preview: str | None
    message_count: int
    unread_count: int  # Unread for the caller-member, 0 if no caller


class UnreadCounts(BaseModel):
    """Sidebar badge data for one member's perspective."""

    member_id: uuid.UUID
    total: int
    by_board: list[BoardSummary]


class NotifyOnFrontSettings(BaseModel):
    """Per-member opt-in toggles for the on-front-start prompt."""

    notify_on_front_global: bool = False
    notify_on_front_self: bool = False
    notify_on_front_member_ids: list[uuid.UUID] = Field(default_factory=list)

    @field_validator("notify_on_front_member_ids")
    @classmethod
    def _dedup(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        seen: set[uuid.UUID] = set()
        out: list[uuid.UUID] = []
        for m in v:
            if m not in seen:
                seen.add(m)
                out.append(m)
        return out


class FrontStartPrompt(BaseModel):
    """Returned by GET /messages/front-start-prompt for the just-fronted
    member. Empty list = nothing to prompt about. The frontend renders a
    toast/modal pointing at /messages."""

    member_id: uuid.UUID
    summaries: list[BoardSummary]
    total_unread: int


class MarkSeenRequest(BaseModel):
    """Mark a board as seen by a specific member."""

    member_id: uuid.UUID
    board_kind: BoardKindLiteral
    board_member_id: uuid.UUID | None = None
