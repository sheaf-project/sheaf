import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class RetentionTrimNoticeRead(BaseModel):
    id: uuid.UUID
    requested_at: datetime
    effective_at: datetime
    from_tier: str
    to_tier: str
    reason: str
    status: str

    model_config = {"from_attributes": True}


class RetentionResponse(BaseModel):
    """Effective + tier-max + override caps, plus any active trim notice.

    Caps semantics: a value of 0 means "unlimited" (selfhosted tier default).
    """

    effective_max_revisions: int
    effective_max_days: int
    tier_max_revisions: int
    tier_max_days: int
    override_revisions: int | None
    override_days: int | None
    trim_notice: RetentionTrimNoticeRead | None = None


class RetentionUpdate(BaseModel):
    """Caller sends only the fields they want to change.

    Reductions (lowering caps) route through SafetyChangeRequest as a
    loosening change — see split_safety_changes. Setting a field to null
    clears the override and falls back to the tier max.
    """

    max_revisions: int | None = Field(default=None, ge=0, le=100000)
    max_revision_days: int | None = Field(default=None, ge=0, le=36500)

    # Re-auth needed for loosening (cap reductions).
    password: str | None = None
    totp_code: str | None = None
