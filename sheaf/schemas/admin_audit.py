"""Schemas for the admin audit-log endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AdminAuditEventRead(BaseModel):
    """Single audit-log row as returned by /v1/admin/audit-events."""

    id: uuid.UUID
    admin_user_id: uuid.UUID | None
    admin_email: str | None
    action: str
    target_type: str
    target_id: uuid.UUID | None
    target_user_id: uuid.UUID | None
    reason: str | None
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserAdminActivityRead(BaseModel):
    """User-facing view: an admin action affecting the caller's
    account. Same row as `AdminAuditEventRead` minus the admin's
    user id (not a useful identifier outside the admin surface) and
    minus the target_user_id (always self when shown to the user)."""

    id: uuid.UUID
    admin_email: str | None
    action: str
    target_type: str
    target_id: uuid.UUID | None
    reason: str | None
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}
