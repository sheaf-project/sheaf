import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# `delete_confirmation` is re-exported here as the System Safety auth tier.
# Historical name retained for API / DB compatibility.
from sheaf.models.system import DeleteConfirmation


class SystemSafetySettings(BaseModel):
    grace_period_days: int
    auth_tier: DeleteConfirmation
    applies_to_members: bool
    applies_to_groups: bool
    applies_to_tags: bool
    applies_to_fields: bool
    applies_to_fronts: bool
    applies_to_journals: bool
    applies_to_images: bool
    applies_to_revisions: bool
    applies_to_notifications: bool
    auto_pin_first_revision: bool


class SystemSafetyUpdate(BaseModel):
    """All fields optional — caller sends only the fields they want to change."""

    grace_period_days: int | None = Field(default=None, ge=0, le=365)
    auth_tier: DeleteConfirmation | None = None
    applies_to_members: bool | None = None
    applies_to_groups: bool | None = None
    applies_to_tags: bool | None = None
    applies_to_fields: bool | None = None
    applies_to_fronts: bool | None = None
    applies_to_journals: bool | None = None
    applies_to_images: bool | None = None
    applies_to_revisions: bool | None = None
    applies_to_notifications: bool | None = None
    auto_pin_first_revision: bool | None = None

    # Re-auth for loosening changes is checked against the *current* auth tier.
    password: str | None = None
    totp_code: str | None = None


class PendingActionRead(BaseModel):
    id: uuid.UUID
    action_type: str
    target_id: uuid.UUID
    target_label: str
    requested_at: datetime
    requested_by_user_id: uuid.UUID | None
    finalize_after: datetime
    fronting_member_ids: list[str]
    fronting_member_names: list[str]
    status: str

    model_config = {"from_attributes": True}


class SafetyChangeRequestRead(BaseModel):
    id: uuid.UUID
    requested_at: datetime
    requested_by_user_id: uuid.UUID | None
    finalize_after: datetime
    changes: dict
    status: str

    model_config = {"from_attributes": True}


class SystemSafetyResponse(BaseModel):
    settings: SystemSafetySettings
    pending_actions: list[PendingActionRead]
    pending_changes: list[SafetyChangeRequestRead]


class SystemSafetyUpdateResponse(BaseModel):
    settings: SystemSafetySettings
    applied: list[str]
    deferred: list[str]
    pending_change: SafetyChangeRequestRead | None = None
